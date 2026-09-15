const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  downloadImages: (data) => ipcRenderer.invoke('download-images', data),
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  getConfig: () => ipcRenderer.invoke('get-config'),
  onProgress: (callback) => {
    ipcRenderer.on('download-progress', (event, data) => callback(data));
  }
});