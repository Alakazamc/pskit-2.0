export type SseFrame = { data: string; event?: string; id?: string; retry?: number };

const MAX_FRAME_BYTES = 1_000_000;

export class SseFrameError extends Error {
  constructor(readonly acceptedFrames: SseFrame[]) {
    super("Event frame too large");
    this.name = "SseFrameError";
  }
}

/** Decode SSE framing independently of the JSON carried in its data fields. */
export class SseFrameDecoder {
  private readonly utf8 = new TextDecoder("utf-8", { ignoreBOM: true });
  private line = "";
  private data: string[] = [];
  private event?: string;
  private id?: string;
  private retry?: number;
  private frameBytes = 0;
  private pendingCR = false;
  private firstLine = true;
  private finished = false;

  push(chunk: Uint8Array): SseFrame[] {
    if (this.finished) throw new Error("SSE decoder already finished");
    const frames: SseFrame[] = [];
    let start = 0;
    for (let index = 0; index < chunk.length; index++) {
      const byte = chunk[index];
      if (this.pendingCR) {
        this.pendingCR = false;
        // Delay CR until its optional LF arrives so both count toward the limit.
        if (byte === 10) {
          this.countBytes(1, frames);
          this.endLine(frames);
          start = index + 1;
          continue;
        }
        this.endLine(frames);
      }
      if (byte !== 10 && byte !== 13) continue;
      this.countBytes(index - start + 1, frames);
      this.line += this.utf8.decode(chunk.subarray(start, index), { stream: true });
      start = index + 1;
      if (byte === 13) this.pendingCR = true;
      else this.endLine(frames);
    }
    this.countBytes(chunk.length - start, frames);
    this.line += this.utf8.decode(chunk.subarray(start), { stream: true });
    return frames;
  }

  finish(): SseFrame[] {
    if (this.finished) return [];
    this.finished = true;
    const frames: SseFrame[] = [];
    if (this.pendingCR) this.endLine(frames);
    // EOF without a blank line does not dispatch a partial event.
    this.utf8.decode();
    this.line = "";
    this.data = [];
    return frames;
  }

  private countBytes(bytes: number, acceptedFrames: SseFrame[]) {
    this.frameBytes += bytes;
    if (this.frameBytes > MAX_FRAME_BYTES) {
      this.finished = true;
      throw new SseFrameError(acceptedFrames);
    }
  }

  private endLine(frames: SseFrame[]) {
    let line = this.line + this.utf8.decode();
    this.line = "";
    if (this.firstLine) {
      this.firstLine = false;
      if (line.startsWith("\ufeff")) line = line.slice(1);
    }
    if (line === "") {
      if (this.data.length) frames.push({
        data: this.data.join("\n"),
        ...(this.event !== undefined ? { event: this.event } : {}),
        ...(this.id !== undefined ? { id: this.id } : {}),
        ...(this.retry !== undefined ? { retry: this.retry } : {}),
      });
      this.data = [];
      this.event = undefined;
      this.id = undefined;
      this.retry = undefined;
      this.frameBytes = 0;
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    else if (field === "id" && !value.includes("\0")) this.id = value;
    else if (field === "retry" && /^[0-9]+$/.test(value) && Number.isSafeInteger(Number(value))) {
      this.retry = Number(value);
    }
  }
}
