export type YouTubeIframePlayer = {
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  playVideo: () => void;
  destroy: () => void;
};

export type YouTubeIframeApi = {
  Player: new (
    target: HTMLIFrameElement,
    options: { events?: { onReady?: (event: { target: YouTubeIframePlayer }) => void } },
  ) => YouTubeIframePlayer;
};

declare global {
  interface Window {
    YT?: YouTubeIframeApi;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let iframeApiPromise: Promise<YouTubeIframeApi> | null = null;

/** Loads the shared YouTube IFrame API without replacing another ready callback. */
export function loadYouTubeIframeApi(): Promise<YouTubeIframeApi> {
  if (typeof window === 'undefined') return Promise.reject(new Error('YouTube player API requires a browser.'));
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (iframeApiPromise) return iframeApiPromise;

  iframeApiPromise = new Promise<YouTubeIframeApi>((resolve, reject) => {
    const previousReady = window.onYouTubeIframeAPIReady;
    const onReady = () => {
      previousReady?.();
      if (window.onYouTubeIframeAPIReady === onReady) window.onYouTubeIframeAPIReady = previousReady;
      if (window.YT?.Player) resolve(window.YT);
      else reject(new Error('YouTube player API loaded without YT.Player.'));
    };
    window.onYouTubeIframeAPIReady = onReady;

    let script = document.querySelector<HTMLScriptElement>('script[data-youtube-iframe-api]');
    if (!script) {
      script = document.createElement('script');
      script.src = 'https://www.youtube.com/iframe_api';
      script.async = true;
      script.dataset.youtubeIframeApi = 'true';
      document.head.append(script);
    }
    script.addEventListener('error', () => reject(new Error('YouTube player API could not be loaded.')), { once: true });
  });

  return iframeApiPromise;
}
