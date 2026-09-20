const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function load(execFile) {
  const scope = { module: {exports:{}}, require: name => name === 'node:child_process' ? {execFile} : require(name),
    process: {platform:'win32',env:{SystemRoot:'C:\\Windows'}},setTimeout,clearTimeout };
  vm.runInNewContext(readFileSync(path.join(__dirname,'../src/process-lifecycle.js'),'utf8'), scope);
  return scope.module.exports;
}
function child() {const p=new EventEmitter();Object.assign(p,{pid:123,exitCode:null,signalCode:null});return p;}
test('normal shutdown uses private stdin and waits; never taskkill', async()=>{
 const p=child();let forced=false;p.stdin={writable:true,write: command=>{assert.equal(command,'shutdown\n');setImmediate(()=>{p.exitCode=0;p.emit('exit',0);});}};
 await load(()=>{forced=true;}).stopProcess(p,'backend',()=>{},1000);assert.equal(forced,false);
});
test('dead PID is never passed to taskkill', async()=>{
 const p=child();p.exitCode=75;await load(()=>{throw Error('recycled PID');}).stopProcess(p,'backend',()=>{},0);
});
test('taskkill denial rejects cleanup instead of pretending success',async()=>{
 const p=child();let command;
 const lifecycle=load((cmd,args,opts,cb)=>{command=cmd;assert.equal(opts.windowsHide,true);cb(Error('denied'),'','access denied');});
 await assert.rejects(lifecycle.stopProcess(p,'backend',()=>{},0),/access denied/);assert.match(command,/System32.*taskkill.exe$/);assert.equal(p.exitCode,null);
});
test('forced termination must be observed before cleanup resolves',async()=>{
 const p=child();let called=false;
 await load((_cmd,_args,_opts,cb)=>{called=true;setImmediate(()=>{p.exitCode=1;p.emit('exit',1);cb(null,'','');});}).stopProcess(p,'backend',()=>{},0);
 assert.equal(called,true);assert.equal(p.exitCode,1);
});
