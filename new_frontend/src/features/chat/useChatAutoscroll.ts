import { useLayoutEffect, useRef, type RefObject } from "react";

export function useChatAutoscroll(
  scroller: RefObject<HTMLDivElement | null>,
  content: RefObject<HTMLDivElement | null>,
  changeKey: string,
  runId?: string | null,
  contentReady = true,
) {
  const following = useRef(true);
  const followNow = useRef<() => void>(() => {});
  useLayoutEffect(() => {
    following.current = true;
  }, [runId]);
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const atBottom = () => element.scrollHeight - element.clientHeight - element.scrollTop <= 96;
    const onScroll = () => {
      // Browser anchoring also emits scroll. Input handlers suspend following;
      // scrolling back to the bottom enables it again.
      if (!following.current) following.current = atBottom();
    };
    const follow = () => {
      if (following.current) element.scrollTop = element.scrollHeight;
    };
    followNow.current = follow;
    const onWheel = (event: WheelEvent) => { if (event.deltaY < 0) following.current = false; };
    const onKey = (event: KeyboardEvent) => {
      if (["ArrowUp", "PageUp", "Home"].includes(event.key)) following.current = false;
    };
    const onPointer = (event: PointerEvent) => { if (event.target === element) following.current = false; };
    let touchY = 0;
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? 0; };
    const onTouchMove = (event: TouchEvent) => {
      const nextY = event.touches[0]?.clientY ?? touchY;
      if (nextY > touchY) following.current = false;
      touchY = nextY;
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("wheel", onWheel, { passive: true });
    element.addEventListener("keydown", onKey);
    element.addEventListener("pointerdown", onPointer);
    element.addEventListener("touchstart", onTouchStart, { passive: true });
    element.addEventListener("touchmove", onTouchMove, { passive: true });
    follow();
    const observer = typeof ResizeObserver !== "undefined" && content.current
      ? new ResizeObserver(follow) : null;
    if (observer && content.current) observer.observe(content.current);
    observer?.observe(element);
    return () => {
      element.removeEventListener("scroll", onScroll);
      element.removeEventListener("wheel", onWheel);
      element.removeEventListener("keydown", onKey);
      element.removeEventListener("pointerdown", onPointer);
      element.removeEventListener("touchstart", onTouchStart);
      element.removeEventListener("touchmove", onTouchMove);
      observer?.disconnect();
      followNow.current = () => {};
    };
  }, [scroller, content, runId, contentReady]);
  useLayoutEffect(() => {
    followNow.current();
  }, [scroller, changeKey]);
}
