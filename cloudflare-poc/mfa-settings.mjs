import {passwordHash,randomToken,totp} from './auth.mjs';
import {base32} from './invitations.mjs';
export const pendingSchema='CREATE TABLE IF NOT EXISTS poc_mfa_pending (user_id TEXT PRIMARY KEY REFERENCES poc_users(id) ON DELETE CASCADE,secret TEXT NOT NULL,expires INTEGER NOT NULL)';
export async function beginMfa(db,user,password,now=Math.floor(Date.now()/1000),issuer='DataFoldIT Test'){
  const row=await db.prepare('SELECT * FROM poc_users WHERE id=? AND active=1').bind(user.id).first();
  if(!row||row.mfa_hex)return null;
  const bucket='mfa:'+row.id;
  await db.prepare('INSERT INTO poc_login_limits(bucket,started,count) VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN started<? THEN 1 ELSE count+1 END,started=CASE WHEN started<? THEN excluded.started ELSE started END').bind(bucket,now,now-900,now-900).run();
  if((await db.prepare('SELECT count FROM poc_login_limits WHERE bucket=?').bind(bucket).first()).count>5)return null;
  if(await passwordHash(password,row.salt)!==row.password_hash)return null;
  const secret=randomToken().slice(0,40);
  await db.prepare('INSERT INTO poc_mfa_pending(user_id,secret,expires) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret,expires=excluded.expires').bind(user.id,secret,now+600).run();
  return {otpauth:'otpauth://totp/'+encodeURIComponent(issuer+':'+row.email)+'?secret='+base32(secret)+'&issuer='+encodeURIComponent(issuer)+'&algorithm=SHA1&digits=6&period=30'};
}
export async function confirmMfa(db,user,code,now=Math.floor(Date.now()/1000)){
  const row=await db.prepare('SELECT * FROM poc_mfa_pending WHERE user_id=? AND expires>?').bind(user.id,now).first();
  if(!row||typeof code!=='string'||!/^\d{6}$/.test(code))return false;
  // One attempt per setup; a wrong code requires re-verifying the password.
  await db.prepare('DELETE FROM poc_mfa_pending WHERE user_id=?').bind(user.id).run();
  let accepted=-1;for(const step of [Math.floor(now/30),Math.floor(now/30)-1,Math.floor(now/30)+1])if(await totp(row.secret,step)===code){accepted=step;break;}
  if(accepted<0)return false;
  const results=await db.batch([
    db.prepare("UPDATE poc_users SET mfa_hex=?,last_step=? WHERE id=? AND active=1 AND mfa_hex=''").bind(row.secret,accepted,user.id),
    db.prepare('DELETE FROM poc_sessions WHERE user_id=?').bind(user.id)
  ]);return results[0].meta.changes===1;
}
export const securityPage=csrf=>`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Security · DataFoldIT Test</title><link rel="stylesheet" href="/poc.css"><main><a href="/">Back to workspace</a><h1>Enable authenticator MFA</h1><p>If MFA is already enabled, no change is needed. Admin MFA cannot be disabled.</p><form id="begin"><label>Confirm your password<input name="password" type="password" autocomplete="current-password" required></label><button>Set up MFA</button></form><form id="confirm" hidden><img id="qr" width="260" height="260" alt="Private authenticator setup QR code"><label>Authenticator code<input name="code" pattern="[0-9]{6}" inputmode="numeric" required></label><button>Enable MFA</button></form><p id="message" role="status"></p><input id="csrf" type="hidden" value="${csrf}"></main><script src="/security.js" defer></script></html>`;
export const securityJs=`const message=document.querySelector('#message');async function send(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('#csrf').value},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw new Error(result.error);return result;}document.querySelector('#begin').addEventListener('submit',async e=>{e.preventDefault();try{const data=await send('/api/security/begin',Object.fromEntries(new FormData(e.target)));e.target.reset();document.querySelector('#qr').src=data.qr;document.querySelector('#confirm').hidden=false;message.textContent='Scan the QR code. Setup expires in 10 minutes.';}catch(err){message.textContent=err.message;}});document.querySelector('#confirm').addEventListener('submit',async e=>{e.preventDefault();try{await send('/api/security/confirm',Object.fromEntries(new FormData(e.target)));e.target.hidden=true;document.querySelector('#qr').removeAttribute('src');message.textContent='MFA enabled. All sessions have been signed out. Return to sign in using the next authenticator code.';}catch(err){e.target.hidden=true;message.textContent=err.message+' Start setup again.';}});`;
