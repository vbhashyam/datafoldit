import {signIn,sessionUser,canWrite} from "./auth.mjs";
import {saveFile,loadFile} from "./free-storage.mjs";
import {createInvitation,beginEnrollment,acceptInvitation,requestInvitationEmail} from './invitations.mjs';
import {loginPage,loginJs,setupPage,setupJs,usersPage,usersJs} from './auth-screens.mjs';
import {sendInvitationMail} from './invitation-mail.mjs';
import {beginMfa,confirmMfa,securityPage,securityJs} from './mfa-settings.mjs';
import qrcode from './node_modules/qrcode-generator/dist/qrcode.mjs';
import {dashboardPage,dashboardJs,recordApi} from './dashboard.mjs';
import {importRequest,downloadImport} from './bulk-import.mjs';
import {changeUser,userControlsJs} from './user-controls.mjs';
import {operationalBackup} from './data-backup.mjs';

const headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer","X-Frame-Options":"DENY"};
const json=(data,status=200,extra={})=>Response.json(data,{status,headers:{...headers,...extra}});
const html=body=>new Response(body,{headers:{...headers,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}});
async function smallJson(request){
  if(!request.headers.get('Content-Type')?.startsWith('application/json'))throw new Error('JSON required');
  const reader=request.body?.getReader();if(!reader)throw new Error('Body required');
  let size=0;const chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>2048){await reader.cancel();throw new Error('Request too large');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return JSON.parse(new TextDecoder().decode(bytes));
}
const cookieToken=request=>(request.headers.get("Cookie")||"").split(";").map(x=>x.trim()).find(x=>x.startsWith("__Host-dfit_poc="))?.slice(16);
const escape=value=>String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const page=(user)=>`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DataFoldIT · Free-hosting prototype</title><link rel="stylesheet" href="/poc.css"><body><main><header><strong>DATAFOLDIT</strong><span>Sample-data prototype · not production</span></header><h1>${user?"Welcome "+escape(user.email.split("@")[0])+"!!":"Sign in to the prototype"}</h1><p>This is a separate migration test. Do not upload real payroll or employee documents.</p>${user?`<section><h2>Sample transactions</h2><div id="transactions"></div>${user.role==="read"?"<p>Read-only access</p>":'<form id="transaction"><label>Description<input name="description" maxlength="120" required></label><label>Amount (USD)<input name="amount" type="number" min="0.01" max="1000000" step=".01" required></label><button>Save sample transaction</button></form><form id="upload"><label>Sample attachment (maximum 5 MiB)<input name="file" type="file" required></label><button>Save sample attachment</button></form>'}<button id="logout">Sign out</button></section><input id="csrf" type="hidden" value="${escape(user.csrf)}">`:'<form id="login"><label>Email<input name="email" type="email" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><label>Authenticator code<input name="code" pattern="[0-9]{6}" autocomplete="one-time-code" required></label><button>Sign in</button></form>'}<p id="message" role="status"></p></main><script src="/poc.js" defer></script></body></html>`;
const css=":root{color-scheme:dark}body{margin:0;background:radial-gradient(ellipse at top right,#12333d,transparent 50%),#0b101c;color:#edf2f7;font:15px system-ui}main{max-width:880px;margin:50px auto;padding:28px}header{display:flex;justify-content:space-between;gap:20px}header span,p{color:#a0acbe}section,form{border:1px solid #ffffff20;border-radius:12px;padding:22px;margin:20px 0;background:#111825}label{display:grid;gap:8px;margin-bottom:16px}input,button{padding:12px;border-radius:7px;border:1px solid #ffffff25;background:#0d1420;color:inherit;font:inherit}button{background:#68d9df;color:#07171c;cursor:pointer}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ffffff20}#message{white-space:pre-wrap}";
const js=`
const message=document.querySelector("#message");
async function send(path,body,raw=false){
 const response=await fetch(path,{method:"POST",headers:{"Content-Type":raw?"application/octet-stream":"application/json","X-CSRF-Token":document.querySelector("#csrf")?.value||""},body:raw?body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(data.error||"Request failed");return data;
}
document.querySelector("#login")?.addEventListener("submit",async e=>{e.preventDefault();try{await send("/api/login",Object.fromEntries(new FormData(e.target)));location.href="/";}catch(err){message.textContent=err.message;}});
document.querySelector("#transaction")?.addEventListener("submit",async e=>{e.preventDefault();try{await send("/api/transactions",Object.fromEntries(new FormData(e.target)));await list();e.target.reset();message.textContent="Sample transaction saved.";}catch(err){message.textContent=err.message;}});
document.querySelector("#upload")?.addEventListener("submit",async e=>{e.preventDefault();try{const file=e.target.elements.file.files[0];if(file.size>5242880)throw new Error("Maximum sample file size is 5 MiB.");const data=await send("/api/files?name="+encodeURIComponent(file.name),file,true);message.replaceChildren(document.createTextNode("Sample file saved. "));const link=document.createElement("a");link.href="/api/files/"+data.id;link.textContent="Download";message.append(link);}catch(err){message.textContent=err.message;}});
document.querySelector("#logout")?.addEventListener("click",async()=>{try{await send("/api/logout",{});location.href="/";}catch(err){message.textContent=err.message;}});
async function list(){const response=await fetch("/api/transactions");if(!response.ok)throw new Error("Sign in again.");const rows=await response.json();const table=document.createElement("table");for(const row of rows){const tr=document.createElement("tr");for(const value of [row.description,(row.amount_cents/100).toFixed(2),row.updated_by]){const td=document.createElement("td");td.textContent=value;tr.append(td);}table.append(tr);}document.querySelector("#transactions").replaceChildren(table);}
if(document.querySelector("#transactions"))list().catch(err=>message.textContent=err.message);
`;
export async function initializeTransactions(db){
  await db.prepare("CREATE TABLE IF NOT EXISTS poc_transactions(id INTEGER PRIMARY KEY,description TEXT NOT NULL,amount_cents INTEGER NOT NULL,updated_by TEXT NOT NULL)").run();
}
export default {
  async fetch(request,env){
    try{
      const url=new URL(request.url);
      if(request.method==="GET"&&url.pathname==="/healthz")return json({status:"ok",prototype:true});
      if(request.method==='GET'&&url.pathname.startsWith('/static/'))return env.ASSETS.fetch(request);
      if(request.method==='GET'&&url.pathname==='/dashboard.js')return new Response(dashboardJs,{headers:{...headers,'Content-Type':'application/javascript'}});
      if(request.method==="GET"&&url.pathname==="/poc.css")return new Response(css,{headers:{...headers,"Content-Type":"text/css"}});
      if(request.method==="GET"&&url.pathname==="/poc.js")return new Response(js,{headers:{...headers,"Content-Type":"application/javascript"}});
      if(request.method==='GET'&&url.pathname==='/accept')return html(setupPage);
      if(request.method==='GET'&&url.pathname==='/login.js')return new Response(loginJs,{headers:{...headers,'Content-Type':'application/javascript'}});
      if(request.method==='GET'&&url.pathname==='/enrollment.js')return new Response(setupJs,{headers:{...headers,'Content-Type':'application/javascript'}});
      if(request.method==='GET'&&url.pathname==='/admin.js')return new Response(usersJs.slice(0,usersJs.indexOf("fetch('/api/admin/users')"))+userControlsJs,{headers:{...headers,'Content-Type':'application/javascript'}});
      if(request.method==='GET'&&url.pathname==='/security.js')return new Response(securityJs,{headers:{...headers,'Content-Type':'application/javascript'}});
      if(request.method==="POST"&&request.headers.get("Origin")!==url.origin)return json({error:"Request origin invalid"},403);
      if(request.method==='POST'&&url.pathname==='/api/access/request'){
        const {email}=await smallJson(request);
        await requestInvitationEmail(env.DB,email,(recipient,token)=>sendInvitationMail(env,recipient,token));
        return json({ok:true});
      }
      if(request.method==='POST'&&url.pathname==='/api/enrollment/begin'){
        const {token}=await smallJson(request),setup=await beginEnrollment(env.DB,token,undefined,env.DEPLOYMENT_ENV==='production'?'DataFoldIT Production':'DataFoldIT Test');
        if(!setup)return json({error:'Invitation unavailable. Ask your administrator for a fresh link.'},400);
        const qr=qrcode(0,'M');qr.addData(setup.otpauth);qr.make();
        return json({...setup,qr:qr.createDataURL(5,20)});
      }
      if(request.method==='POST'&&url.pathname==='/api/enrollment/accept'){
        const {token,password,code,skipMfa}=await smallJson(request);
        return await acceptInvitation(env.DB,token,password,code,undefined,skipMfa===true)?json({ok:true}):json({error:'Setup failed. Check your password and authenticator code, or ask for a new invitation.'},400);
      }
      if(request.method==="POST"&&url.pathname==="/api/login"){
        const {email,password,code}=await smallJson(request),session=await signIn(env.DB,email,password,code);
        if(!session)return json({error:"Unable to sign in. Check credentials or try again later."},401);
        return json({ok:true},200,{"Set-Cookie":"__Host-dfit_poc="+session.token+"; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=28800"});
      }
      const token=cookieToken(request),user=await sessionUser(env.DB,token);
      if(request.method==='GET'&&(url.pathname==='/'||url.pathname==='/login')&&!user)return html(loginPage);
      if(request.method==='GET'&&url.pathname==='/'&&user)return html(dashboardPage(user).replace('<body>','<link rel="stylesheet" href="/static/financial-views.css"><body>'));
      if(!user)return json({error:"Sign in required"},401);
      if(request.method==='GET'&&url.pathname==='/api/operational-backup')return user.role==='admin'?operationalBackup(env.DB,env.DEPLOYMENT_ENV==='production'):json({error:'Administrator access required'},403);
      if(url.pathname==='/api/import')return importRequest(request,env.DB,user);
      if(request.method==='GET'&&url.pathname==='/api/attachment-links')return json((await env.DB.prepare('SELECT a.record_id,f.id,f.name FROM record_attachments a JOIN import_files f ON a.file_id=f.id').all()).results);
      if(request.method==='GET'&&url.pathname.startsWith('/api/import-files/')){const file=await downloadImport(env.DB,url.pathname.split('/').at(-1));if(!file)return json({error:'Not found'},404);return new Response(file.bytes,{headers:{...headers,'Content-Type':'application/octet-stream','Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent(file.name),'Content-Security-Policy':'sandbox'}});}
      if(url.pathname==='/api/records')return recordApi(request,env.DB,user,request.method==='POST'?await smallJson(request):null);
      if(request.method==='GET'&&url.pathname==='/security')return html(securityPage(user.csrf));
      if(request.method==='POST'&&url.pathname.startsWith('/api/security/')){
        if(request.headers.get('X-CSRF-Token')!==user.csrf)return json({error:'Invalid request'},403);
        const data=await smallJson(request);
        if(url.pathname==='/api/security/begin'){
          const setup=await beginMfa(env.DB,user,data.password,undefined,env.DEPLOYMENT_ENV==='production'?'DataFoldIT Production':'DataFoldIT Test');if(!setup)return json({error:'Check your password, or MFA may already be enabled.'},400);
          const qr=qrcode(0,'M');qr.addData(setup.otpauth);qr.make();return json({...setup,qr:qr.createDataURL(5,20)});
        }
        if(url.pathname==='/api/security/confirm')return await confirmMfa(env.DB,user,data.code)?json({ok:true}):json({error:'Invalid or expired setup code.'},400);
      }
      if(url.pathname==='/admin'||url.pathname.startsWith('/api/admin/')){
        if(user.role!=='admin')return json({error:'Administrator access required'},403);
        if(request.method==='GET'&&url.pathname==='/admin')return html(usersPage(user.csrf).replace('<main>','<link rel="stylesheet" href="/static/financial-views.css"><main>'));
        if(request.method==='POST'&&url.pathname==='/api/admin/user-access'){try{await changeUser(env.DB,user,request.headers.get('X-CSRF-Token'),await smallJson(request));return json({ok:true});}catch(e){return json({error:e.message},400);}}
        if(request.method==='GET'&&url.pathname==='/api/admin/users')return json((await env.DB.prepare('SELECT id,email,role,active FROM poc_users ORDER BY email').all()).results);
        if(request.method==='POST'&&url.pathname==='/api/admin/invitations'){
          if(!canWrite(user,request.headers.get('X-CSRF-Token')))return json({error:'Invalid request'},403);
          const {email,role}=await smallJson(request),invite=await createInvitation(env.DB,user,request.headers.get('X-CSRF-Token'),email,role);
          try{await sendInvitationMail(env,invite.email,invite.token);}catch{return json({error:'Email approved, but setup email could not be sent. Retry after checking email settings.'},503);}
          return json({email:invite.email,emailSent:true},201);
        }
        return json({error:'Not found'},404);
      }
      if(request.method==="POST"&&url.pathname==="/api/logout"){
        if(request.headers.get("X-CSRF-Token")!==user.csrf)return json({error:"Invalid request"},403);
        const {digest}=await import("./auth.mjs");
        await env.DB.prepare("DELETE FROM poc_sessions WHERE hash=?").bind(await digest(token)).run();
        return json({ok:true},200,{"Set-Cookie":"__Host-dfit_poc=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"});
      }
      if(request.method==="GET"&&url.pathname==="/api/transactions"){
        const result=await env.DB.prepare("SELECT * FROM poc_transactions ORDER BY id DESC LIMIT 100").all();return json(result.results);
      }
      if(request.method==="GET"&&url.pathname.startsWith("/api/files/")){
        const file=await loadFile(env.DB,user.id,url.pathname.split("/").at(-1));
        if(!file)return json({error:"Not found"},404);
        return new Response(file.bytes,{headers:{...headers,"Content-Type":"application/octet-stream","Content-Disposition":"attachment; filename*=UTF-8''"+encodeURIComponent(file.name),"Content-Security-Policy":"sandbox"}});
      }
      if(request.method!=="POST")return json({error:"Not found"},404);
      if(!canWrite(user,request.headers.get("X-CSRF-Token")))return json({error:"Write permission and valid CSRF token required"},403);
      if(url.pathname==="/api/transactions"){
        const data=await smallJson(request),amount=String(data.amount);
        if(typeof data.description!=="string"||!data.description.trim()||data.description.length>120||!/^\d{1,7}(\.\d{1,2})?$/.test(amount))return json({error:"Invalid sample transaction"},400);
        const cents=Math.round(Number(amount)*100);if(cents<=0||cents>100000000)return json({error:"Invalid amount"},400);
        const result=await env.DB.prepare("INSERT INTO poc_transactions(description,amount_cents,updated_by) SELECT ?,?,? WHERE (SELECT COUNT(*) FROM poc_transactions)<1000").bind(data.description.trim(),cents,user.email).run();
        return result.meta.changes?json({ok:true},201):json({error:"Sample transaction limit reached"},409);
      }
      if(url.pathname==="/api/files"){
        const length=Number(request.headers.get("Content-Length"));if(!Number.isFinite(length)||length<=0||length>5242880)return json({error:"Maximum test file size is 5 MiB"},413);
        const bytes=new Uint8Array(await request.arrayBuffer());if(bytes.length>5242880)return json({error:"File too large"},413);
        return json({id:await saveFile(env.DB,user.id,url.searchParams.get("name"),bytes)},201);
      }
      return json({error:"Not found"},404);
    }catch(error){
      // Report only a fixed category, never database text, user input or secrets.
      const category=/pbkdf2|derivebits|iterations/i.test(String(error.message))?'password_runtime':/d1|sqlite/i.test(String(error.message))?'database_runtime':'request_runtime';
      return json({error:"The request could not be completed. Check input or test storage limits.",category},400);
    }
  }
};
