const MENU_ID = "yt-whisper-add-link";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Add to YT Whisper queue",
    contexts: ["link"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info) => {
  if (info.menuItemId !== MENU_ID || !info.linkUrl) return;

  try {
    const response = await fetch("http://127.0.0.1:8765/add", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: info.linkUrl})
    });
    const data = await response.json();

    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: "YT Whisper Queue",
      message: data.added ? "Link added to transcription queue." : (data.reason || "Link was not added.")
    });
  } catch (err) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icon128.png",
      title: "YT Whisper Queue",
      message: "Queue helper is not running."
    });
  }
});
