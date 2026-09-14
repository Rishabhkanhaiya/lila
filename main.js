const { app, BrowserWindow, ipcMain, screen, Tray, Menu, dialog } = require('electron');
const path = require('path');
const fs = require('fs');

// Set Windows AppUserModelId early for taskbar icon and group pinning
app.setAppUserModelId('com.jarvis.lila.companion');

// Ensure single instance to prevent cache collisions and access denied errors
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  process.exit(0);
}

// Stable hardware acceleration for Three.js WebGL on Windows
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('enable-webgl');
app.commandLine.appendSwitch('use-angle', 'd3d11');
app.commandLine.appendSwitch('disable-gpu-sandbox');
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');


let win = null;
let tray = null;
const configPath = path.join(__dirname, 'config.json');

function loadConfig() {
  try {
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf8'));
    }
  } catch (e) {
    console.error('[Config] Error loading config.json:', e);
  }
  return { width: 480, height: 640, x: null, y: null, opacity: 1.0, ws_url: "ws://127.0.0.1:8765" };
}

function saveConfig(updated) {
  try {
    const current = loadConfig();
    const merged = { ...current, ...updated };
    fs.writeFileSync(configPath, JSON.stringify(merged, null, 2), 'utf8');
  } catch (e) {
    console.error('[Config] Error saving config.json:', e);
  }
}

function createWindow() {
  const config = loadConfig();
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth, height: screenHeight } = primaryDisplay.workAreaSize;

  // Default position: bottom-right corner above taskbar
  const defaultWidth = config.width || 480;
  const defaultHeight = config.height || 640;
  const defaultX = screenWidth - defaultWidth - 20;
  const defaultY = screenHeight - defaultHeight - 20;

  const posX = (config.x !== null && config.x !== undefined && config.x >= 0 && config.x < screenWidth)
    ? config.x
    : defaultX;
  const posY = (config.y !== null && config.y !== undefined && config.y >= 0 && config.y < screenHeight)
    ? config.y
    : defaultY;

  app.setAppUserModelId('com.jarvis.lila.companion');

  const iconPath = path.join(__dirname, 'assets', 'lila_logo.png');

  win = new BrowserWindow({
    title: 'Lila Desktop Companion',
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    width: defaultWidth,
    height: defaultHeight,
    minWidth: 340,
    minHeight: 60,
    x: posX,
    y: posY,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: true,
    hasShadow: true,
    focusable: true,
    show: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
      backgroundThrottling: false
    }
  });

  // Force Windows Taskbar to add tab for Lila
  win.setSkipTaskbar(false);
  win.setTitle('Lila Desktop Companion');

  // Sit above other always-on-top apps and stay visible in fullscreen/maximized
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.showInactive();
  win.flashFrame(false);

  // Default to interactive so all buttons and chat inputs respond to clicks immediately
  win.setIgnoreMouseEvents(false);

  const logPath = path.join(__dirname, '..', 'scratch', 'renderer_console.log');
  win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    try { fs.appendFileSync(logPath, `[Renderer ${level}] ${message} (${sourceId}:${line})\n`); } catch(e) {}
  });
  win.webContents.on('did-fail-load', (_e, code, desc, url) => {
    try { fs.appendFileSync(logPath, `[did-fail-load] ${code} ${desc} ${url}\n`); } catch(e) {}
  });
  win.webContents.on('render-process-gone', (_e, details) => {
    try { fs.appendFileSync(logPath, `[render-process-gone] ${JSON.stringify(details)}\n`); } catch(e) {}
  });

  let selectedModel = process.env.LILA_MODEL || 'ana';
  for (const arg of process.argv) {
    if (arg.startsWith('--model=')) {
      selectedModel = arg.split('=')[1];
    }
  }

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'), {
    query: { model: selectedModel }
  });

  win.once('ready-to-show', () => {
    win.showInactive();
    win.flashFrame(false);
    win.setAlwaysOnTop(true, 'screen-saver');
  });

  // Persist position when moved (debounced to avoid blocking drag loop)
  let saveTimer = null;
  win.on('moved', () => {
    if (!win || win.isDestroyed()) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (win && !win.isDestroyed()) {
        const [x, y] = win.getPosition();
        saveConfig({ x, y });
      }
    }, 400);
  });

  // Global 3D desktop mouse parallax tracking (polls screen cursor at 30 FPS)
  let cursorInterval = setInterval(() => {
    if (!win || win.isDestroyed()) return;
    try {
      const cursor = screen.getCursorScreenPoint();
      const [wx, wy] = win.getPosition();
      const [ww, wh] = win.getSize();
      const cx = wx + ww / 2;
      const cy = wy + wh / 2;
      const primary = screen.getPrimaryDisplay();
      const sw = (primary && primary.bounds) ? primary.bounds.width : 1920;
      const sh = (primary && primary.bounds) ? primary.bounds.height : 1080;
      const normX = Math.max(-1.0, Math.min(1.0, (cursor.x - cx) / (sw * 0.45)));
      const normY = Math.max(-1.0, Math.min(1.0, (cursor.y - cy) / (sh * 0.45)));
      win.webContents.send('global-mouse-move', { x: normX, y: normY });
    } catch (e) {}
  }, 33);

  win.on('closed', () => {
    clearInterval(cursorInterval);
  });
}

// ─── IPC Handlers (registered ONCE, outside createWindow) ─────────────────────

ipcMain.on('set-ignore-mouse-events', (event, ignore) => {
  const w = BrowserWindow.fromWebContents(event.sender);
  if (w && !w.isDestroyed()) {
    if (ignore) {
      w.setIgnoreMouseEvents(true, { forward: true });
    } else {
      w.setIgnoreMouseEvents(false);
    }
  }
});

ipcMain.on('move-window', (_event, { deltaX, deltaY }) => {
  if (win && !win.isDestroyed()) {
    const [x, y] = win.getPosition();
    win.setPosition(Math.round(x + deltaX), Math.round(y + deltaY));
  }
});

ipcMain.on('save-position', (_event, { x, y }) => {
  saveConfig({ x, y });
});

ipcMain.on('bring-to-front', () => {
  if (win && !win.isDestroyed()) {
    if (win.isMinimized()) win.restore();
    win.showInactive();
    win.flashFrame(false);
    win.setAlwaysOnTop(true, 'screen-saver');
  }
});

ipcMain.on('minimize-window', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender);
  if (w && !w.isDestroyed()) w.minimize();
});

ipcMain.on('close-window', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender);
  if (w && !w.isDestroyed()) w.hide();
});

let isMiniMode = false;
let preMiniSize = [480, 640];
ipcMain.on('toggle-mini-mode', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender);
  if (w && !w.isDestroyed()) {
    isMiniMode = !isMiniMode;
    if (isMiniMode) {
      preMiniSize = w.getSize();
      w.setSize(360, 64, true);
    } else {
      w.setSize(preMiniSize[0] || 480, preMiniSize[1] || 640, true);
    }
    w.webContents.send('mini-mode-changed', isMiniMode);
  }
});

ipcMain.handle('get-config', () => {
  return loadConfig();
});

ipcMain.handle('dialog:open-vrm', async () => {
  if (!win || win.isDestroyed()) return null;
  const { canceled, filePaths } = await dialog.showOpenDialog(win, {
    title: 'Select VRM 3D Avatar Model',
    filters: [{ name: 'VRM 3D Avatar (*.vrm)', extensions: ['vrm'] }],
    properties: ['openFile']
  });
  if (!canceled && filePaths.length > 0) {
    return filePaths[0];
  }
  return null;
});

// ─── App Lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();

  // Snapshot watchdog: if capture.trigger exists, save window snapshot to scratch/lila_frame.png
  setInterval(async () => {
    const triggerPath = path.join(__dirname, '..', 'capture.trigger');
    if (fs.existsSync(triggerPath) && win && !win.isDestroyed()) {
      try {
        fs.unlinkSync(triggerPath);
        const img = await win.capturePage();
        const pngBuf = img.toPNG();
        const outPath = path.join(__dirname, '..', 'scratch', 'lila_frame.png');
        fs.writeFileSync(outPath, pngBuf);
        try { fs.appendFileSync(path.join(__dirname, '..', 'scratch', 'renderer_console.log'), `[Snapshot] Saved frame: ${pngBuf.length} bytes\n`); } catch(e) {}
      } catch (err) {
        try { fs.appendFileSync(path.join(__dirname, '..', 'scratch', 'renderer_console.log'), `[Snapshot Error] ${err.message}\n`); } catch(e) {}
      }
    }
  }, 200);

  // Eval watchdog: executes arbitrary JS in renderer for live 3D diagnostics
  setInterval(async () => {
    const evalPath = path.join(__dirname, '..', 'scratch', 'eval.js');
    if (fs.existsSync(evalPath) && win && !win.isDestroyed()) {
      try {
        const code = fs.readFileSync(evalPath, 'utf8');
        fs.unlinkSync(evalPath);
        const res = await win.webContents.executeJavaScript(code);
        fs.writeFileSync(path.join(__dirname, '..', 'scratch', 'eval_result.json'), JSON.stringify(res ?? null, null, 2));
      } catch (err) {
        fs.writeFileSync(path.join(__dirname, '..', 'scratch', 'eval_result.json'), JSON.stringify({ error: err.message }, null, 2));
      }
    }
  }, 200);

  app.on('second-instance', () => {
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.showInactive();
      win.flashFrame(false);
      win.setAlwaysOnTop(true, 'screen-saver');
    } else {
      createWindow();
    }
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', () => {
  setTimeout(() => {
    app.exit(0);
  }, 400).unref();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
    setTimeout(() => {
      app.exit(0);
    }, 400).unref();
  }
});
