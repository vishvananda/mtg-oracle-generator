// Both large models crashed independently on an iPhone during loading.
// iPadOS can identify as macOS; include its touch-capable desktop user agent.
// This is an explicitly conservative product restriction, not a RAM measurement.
export const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
export const isMobileDevice = isIOS || /Android|Mobile/.test(navigator.userAgent) || navigator.userAgentData?.mobile === true;
export const mobileModelMessage = 'These large models currently exceed tested iPhone browser memory limits. Use a computer for local generation. Card previews and editing remain available.';
