const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api',{
  listFolders: ()=> ipcRenderer.invoke('list-folders'),
  listImages: (f)=> ipcRenderer.invoke('list-images', f)
});
