const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

const ROOT = '\\\\192.168.1.3\\エロ漫画';

function createWindow(){
  const win = new BrowserWindow({
    width:1280,
    height:800,
    webPreferences:{
      preload: path.join(__dirname,'preload.js')
    }
  });
  win.loadFile('index.html');
}

ipcMain.handle('list-folders', ()=>{
  return fs.readdirSync(ROOT, { withFileTypes:true })
    .filter(d=>d.isDirectory())
    .map(d=>d.name);
});

ipcMain.handle('list-images', (e, folder)=>{
  const dir = path.join(ROOT, folder);
  return fs.readdirSync(dir)
    .filter(f=>f.match(/\.(jpg|png|webp)$/i));
});

app.whenReady().then(createWindow);
