const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('lilaAPI', {
  setIgnoreMouseEvents: (ignore) => {
    ipcRenderer.send('set-ignore-mouse-events', ignore);
  },
  moveWindow: (deltaX, deltaY) => {
    ipcRenderer.send('move-window', { deltaX, deltaY });
  },
  savePosition: (x, y) => {
    ipcRenderer.send('save-position', { x, y });
  },
  getConfig: () => {
    return ipcRenderer.invoke('get-config');
  },
  sendAction: (action) => {
    ipcRenderer.send('send-action', action);
  },
  onExclusiveFullscreenState: (callback) => {
    ipcRenderer.on('fullscreen-state-change', (_event, isFullscreen) => callback(isFullscreen));
  },
  onGlobalMouseMove: (callback) => {
    ipcRenderer.on('global-mouse-move', (_event, pos) => callback(pos));
  },
  openVRMDialog: () => {
    return ipcRenderer.invoke('dialog:open-vrm');
  },
  bringToFront: () => {
    ipcRenderer.send('bring-to-front');
  },
  minimizeWindow: () => {
    ipcRenderer.send('minimize-window');
  },
  closeWindow: () => {
    ipcRenderer.send('close-window');
  }
});
