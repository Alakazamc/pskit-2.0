import { describe, expect, it } from "vitest";
import { SseFrameDecoder } from "./sse";

const encoder = new TextEncoder();

describe("SSE frame decoding", () => {
  it.each(["\n", "\r", "\r\n"])("preserves data at every byte split for %j line endings", (newline) => {
    const bytes = encoder.encode(`: keepalive${newline}event: message.delta${newline}id: 3${newline}data: 中文🧬${newline}data: next${newline}${newline}`);
    for (let split = 0; split <= bytes.length; split++) {
      const decoder = new SseFrameDecoder();
      const frames = [...decoder.push(bytes.slice(0, split)), ...decoder.push(bytes.slice(split)), ...decoder.finish()];
      expect(frames, `split at byte ${split}`).toEqual([{ data: "中文🧬\nnext", event: "message.delta", id: "3" }]);
    }
  });

  it("handles CRLF and UTF-8 split across every individual byte", () => {
    const decoder = new SseFrameDecoder();
    const frames = [...encoder.encode("data: 中文🧬\r\n\r\n")].flatMap((byte) => decoder.push(new Uint8Array([byte])));
    expect([...frames, ...decoder.finish()]).toEqual([{ data: "中文🧬" }]);
  });

  it("ignores comments and unknown fields while preserving empty data and only removing one space", () => {
    const decoder = new SseFrameDecoder();
    expect(decoder.push(encoder.encode(": comment\nunknown: ignored\n\ndata:\ndata:  indented\n\n")))
      .toEqual([{ data: "\n indented" }]);
    expect(decoder.finish()).toEqual([]);
  });

  it("keeps frame metadata independent and ignores invalid id and retry values", () => {
    const decoder = new SseFrameDecoder();
    expect(decoder.push(encoder.encode("id: 1\nid: invalid\0id\nretry: 42\nretry: 1.5\ndata: first\n\ndata: second\n\n")))
      .toEqual([{ data: "first", id: "1", retry: 42 }, { data: "second" }]);
  });

  it("strips only the initial UTF-8 BOM", () => {
    const decoder = new SseFrameDecoder();
    expect(decoder.push(encoder.encode("\ufeffdata: first\n\ndata: \ufeffsecond\n\n")))
      .toEqual([{ data: "first" }, { data: "\ufeffsecond" }]);
  });

  it("finishes a pending CR delimiter but discards an unterminated frame", () => {
    const decoder = new SseFrameDecoder();
    expect(decoder.push(encoder.encode("data: complete\r\r"))).toEqual([]);
    expect(decoder.finish()).toEqual([{ data: "complete" }]);
    expect(decoder.finish()).toEqual([]);
    const incomplete = new SseFrameDecoder();
    expect(incomplete.push(encoder.encode("data: partial\n"))).toEqual([]);
    expect(incomplete.finish()).toEqual([]);
  });

  it.each(["\n\n", "\r\n\r\n"])("accepts exactly one million bytes including %j delimiters", (ending) => {
    const decoder = new SseFrameDecoder();
    const data = "x".repeat(1_000_000 - 6 - ending.length);
    const frames = [...decoder.push(encoder.encode(`data: ${data}${ending}`)), ...decoder.finish()];
    expect(frames).toHaveLength(1);
    expect(frames[0].data.length).toBe(data.length);
  });

  it("rejects an oversized complete frame before dispatch", () => {
    const decoder = new SseFrameDecoder();
    expect(() => decoder.push(encoder.encode(`data: ${"x".repeat(1_000_000)}\n\n`)))
      .toThrow("Event frame too large");
  });

  it("checks partial frame size before the remaining delimiter arrives", () => {
    const decoder = new SseFrameDecoder();
    expect(decoder.push(encoder.encode(`data: ${"x".repeat(999_994)}`))).toEqual([]);
    expect(() => decoder.push(encoder.encode("x"))).toThrow("Event frame too large");
  });

  it("counts UTF-8 bytes rather than JavaScript character length", () => {
    const decoder = new SseFrameDecoder();
    expect(() => decoder.push(encoder.encode(`data: ${"中".repeat(333_332)}\n\n`)))
      .toThrow("Event frame too large");
  });

  it("resets the limit for each frame within a large chunk", () => {
    const decoder = new SseFrameDecoder();
    const data = "x".repeat(600_000);
    const frames = decoder.push(encoder.encode(`data: ${data}\n\ndata: ${data}\n\n`));
    expect(frames.map((frame) => frame.data.length)).toEqual([600_000, 600_000]);
  });

  it.each(["", "\n\n"])("preserves completed frames in a size error before an oversized %j tail", (ending) => {
    const decoder = new SseFrameDecoder();
    let error: unknown;
    try {
      decoder.push(encoder.encode(`data: first\n\ndata: second\n\ndata: ${"x".repeat(1_000_000)}${ending}`));
    } catch (caught) {
      error = caught;
    }
    expect(error).toMatchObject({ name: "SseFrameError", message: "Event frame too large",
      acceptedFrames: [{ data: "first" }, { data: "second" }] });
  });
});
