import { createServer } from '../node_modules/vite/dist/node/index.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
let restored = false;
const saved = { revision_id:'saved', content_version:'restored-version', sequence:1, reason:'checkpoint', label:'完整收据', transaction_id:'tx-saved', parent_revision_id:null, created_at:new Date().toISOString() };
function view(q, historic) {
  const isRestored = historic || restored;
  const styled = historic || (q.get('facets') || '').split(',').includes('presentation');
  const sheet = q.get('sheet') || (isRestored ? '收据' : '临时表');
  if (isRestored && sheet !== '收据') return { error: { code:'SHEET_NOT_FOUND', error:'工作表不存在' } };
  const cells = {};
  const put = (r,c,v,s,f) => cells[`${r},${c}`] = {t:typeof v==='number'?'n':v==null?'z':'s',v,cached:f?'no':'yes',...(f?{f}:{}),...(styled&&s?{s}:{})};
  const blue = {bg:{rgb:'#195D85'},cl:{rgb:'#FFFFFF'},ht:2,vt:2,bl:1,fs:22};
  if (sheet === '临时表') { put(1,1,'恢复后应移除的临时表',{bg:{rgb:'#FFA500'}}); put(12,6,99999); }
  else {
    put(1,1,'收 款 收 据',blue); put(2,1,'RECEIPT',{...blue,fs:11});
    for (let c=1;c<=6;c++) put(5,c,['序号','项目','规格','数量','单价(元)','金额(元)'][c-1],{...blue,fs:11});
    for (let r=6;r<=10;r++) {
      put(r,1,r-5); put(r,2,['人体工学椅','办公桌面台灯','机械键盘','无线蓝牙耳机','桌面收纳架'][r-6]);
      put(r,3,'标准款'); put(r,4,[6,8,4,5,3][r-6]); put(r,5,[128,158,349,299,89][r-6],{n:{pattern:'0.00'}}); put(r,6,null,{n:{pattern:'0.00'}},`=D${r}*E${r}`);
    }
    put(12,1,'合计（含税）',{bl:1,bg:{rgb:'#E7F1F5'}}); put(12,6,null,{bl:1,n:{pattern:'0.00'}},'=SUM(F6:F10)');
  }
  const merges = [{min_row:1,min_col:1,max_row:1,max_col:6},{min_row:2,min_col:1,max_row:2,max_col:6},{min_row:12,min_col:1,max_row:12,max_col:5}];
  const names = isRestored ? ['收据'] : ['临时表','收据'];
  return {file:{workspaceKey:'id:browser-ws',relative:'history-demo.xlsx'},content_version:isRestored?'restored-version':'before-version',active_sheet:sheet,with_styles:styled,
    sheets:names.map(name=>({name,sheet_id:name,used:{rows:16,cols:6}})),regions:[{sheet,rect:{r0:1,c0:1,r1:200,c1:50},cells,merges:styled&&sheet==='收据'?merges:[],col_widths:styled?{A:8,B:24,C:18,D:10,E:14,F:16}:{},row_heights:styled?{1:40,2:24}:{}}],coverage:{loaded:[{sheet,r0:1,c0:1,r1:200,c1:50}],unloaded:[]}};
}
const server = await createServer({root,configFile:false,resolve:{alias:[{find:'@/lib/univer-modules',replacement:path.join(root,'src/__tests__/fixtures/univer-modules-browser.ts')},{find:'@',replacement:path.join(root,'src')}]},esbuild:{jsx:'automatic'},define:{'process.env.NODE_ENV':JSON.stringify('development')},server:{host:'127.0.0.1',port:5182,strictPort:true},plugins:[{name:'history-fixture',configureServer(server){
  server.middlewares.use(async(req,res,next)=>{
    if(!req.url?.startsWith('/api/v1/')) return next();
    const url=new URL(req.url,'http://localhost'); const q=url.searchParams;
    let payload;
    if(url.pathname==='/api/v1/workbooks/observe') { payload=view(q,false); if(payload.error){res.statusCode=404;payload=payload.error;} }
    else if(url.pathname==='/api/v1/revisions') payload={path:'history-demo.xlsx',content_version:restored?'restored-version':'before-version',revisions:[saved],total:1};
    else if(url.pathname==='/api/v1/revisions/preview') payload={...view(q,true),revision_id:saved.revision_id,revision_reason:saved.reason,revision_label:saved.label};
    else if(url.pathname==='/api/v1/revisions/restore') {
      let raw=''; for await(const chunk of req) raw+=chunk;
      const request=JSON.parse(raw);
      if(request.expected_version!==(restored?'restored-version':'before-version')) {res.statusCode=409;payload={error:'VERSION_CONFLICT',message:'版本已变化'};}
      else {restored=true;payload={status:'ok',path:'history-demo.xlsx',content_version:'restored-version',restored_revision:'saved'};}
    } else { res.statusCode=404;payload={detail:'fixture route unavailable'}; }
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(payload));
  });
}}]});
await server.listen();console.log('http://127.0.0.1:5182/src/__tests__/fixtures/revision-browser.html');
