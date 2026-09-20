function parseWebUrl(value) {
  try {
    const url = new URL(value);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password) return url;
  } catch {}
  return null;
}

function isAppUrl(value, frontendUrl) {
  const url = parseWebUrl(value);
  const frontend = parseWebUrl(frontendUrl);
  return !!url && !!frontend && url.origin === frontend.origin;
}

function isOAuthUrl(value) {
  const url = parseWebUrl(value);
  return !!url && (url.origin === "https://auth.openai.com" || (
    ["http://localhost:1455", "http://127.0.0.1:1455"].includes(url.origin)
    && url.pathname === "/auth/callback"
  ));
}

function configureWindowNavigation(window, { getFrontendUrl, openExternal, onError, oauthPreload }) {
  const openInBrowser = (url) => {
    if (parseWebUrl(url)) Promise.resolve().then(() => openExternal(url)).catch(onError);
  };
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isOAuthUrl(url)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          autoHideMenuBar: true,
          webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, preload: oauthPreload },
        },
      };
    }
    if (isAppUrl(url, getFrontendUrl())) {
      void window.loadURL(url).catch(onError);
    } else {
      openInBrowser(url);
    }
    return { action: "deny" };
  });
  const guardMain = (event, url) => {
    if (isAppUrl(url, getFrontendUrl())) return;
    event.preventDefault();
    openInBrowser(url);
  };
  window.webContents.on("will-navigate", guardMain);
  window.webContents.on("will-redirect", guardMain);
  window.webContents.on("did-create-window", (popup) => {
    // Preserve window.opener through HTTPS identity-provider redirects and the
    // loopback callback; never let a child inherit the workspace's native bridge.
    popup.webContents.setWindowOpenHandler(({ url }) => {
      openInBrowser(url);
      return { action: "deny" };
    });
    const guardPopup = (event, url) => {
      const parsed = parseWebUrl(url);
      if (parsed?.protocol === "https:" || isOAuthUrl(url) || isAppUrl(url, getFrontendUrl())) return;
      event.preventDefault();
    };
    popup.webContents.on("will-navigate", guardPopup);
    popup.webContents.on("will-redirect", guardPopup);
  });
}

function initialWindowBounds(workArea) {
  const width = Math.min(1440, workArea.width);
  const height = Math.min(900, workArea.height);
  return {
    width, height,
    minWidth: Math.min(1024, width),
    minHeight: Math.min(700, height),
    x: workArea.x + Math.floor((workArea.width - width) / 2),
    y: workArea.y + Math.floor((workArea.height - height) / 2),
  };
}

module.exports = { configureWindowNavigation, initialWindowBounds, isAppUrl };
