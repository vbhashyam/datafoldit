// OWASP scrypt configuration: N=2^15, r=8, p=3 (32 MiB).
// This isolated test has no migrated production credentials.
import {scrypt} from 'node:crypto';
const encoder=new TextEncoder();
const hex=bytes=>Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,"0")).join("");
export const randomToken=()=>hex(crypto.getRandomValues(new Uint8Array(32)));
export const digest=async value=>hex(await crypto.subtle.digest("SHA-256",encoder.encode(value)));
const equal=(a,b)=>{
  if(typeof a!=="string"||typeof b!=="string"||a.length!==b.length)return false;
  let difference=0; for(let i=0;i<a.length;i++)difference|=a.charCodeAt(i)^b.charCodeAt(i);
  return difference===0;
};
export async function passwordHash(password,salt) {
  if(typeof password!=="string"||password.length<12||password.length>128)throw new Error("Invalid password length");
  return new Promise((resolve,reject)=>scrypt(password,salt,32,{N:32768,r:8,p:3,maxmem:64*1024*1024},(error,key)=>error?reject(error):resolve('scrypt:32768:8:3:'+hex(key))));
}
export async function totp(secretHex,step) {
  const secret=Uint8Array.from(secretHex.match(/../g),x=>parseInt(x,16));
  const key=await crypto.subtle.importKey("raw",secret,{name:"HMAC",hash:"SHA-1"},false,["sign"]);
  const counter=new Uint8Array(8);new DataView(counter.buffer).setBigUint64(0,BigInt(step));
  const bytes=new Uint8Array(await crypto.subtle.sign("HMAC",key,counter));
  const p=bytes.at(-1)&15;
  const n=((bytes[p]&127)<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3];
  return String(n%1000000).padStart(6,"0");
}
export async function initializeAuth(db) {
  for(const sql of [
    "CREATE TABLE IF NOT EXISTS poc_users (id TEXT PRIMARY KEY,email TEXT UNIQUE COLLATE NOCASE,role TEXT NOT NULL CHECK(role IN ('admin','write','read')),salt TEXT NOT NULL,password_hash TEXT NOT NULL,mfa_hex TEXT NOT NULL,last_step INTEGER NOT NULL DEFAULT -1,active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS poc_sessions (hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES poc_users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS poc_login_limits (bucket TEXT PRIMARY KEY,started INTEGER NOT NULL,count INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS poc_sessions_expiry ON poc_sessions(expires)"
  ])await db.prepare(sql).run();
}
// Internal provisioning only. No public signup endpoint.
export async function provisionUser(db,email,password,role,secret) {
  if(!["admin","read","write"].includes(role)||!/^\S+@\S+\.\S+$/.test(email))throw new Error("Invalid user");
  if(!/^[a-f0-9]{40}$/.test(secret)&&!(secret===''&&role!=='admin'))throw new Error("Invalid MFA key");
  const salt=randomToken(),id=crypto.randomUUID(),hash=await passwordHash(password,salt);
  await db.prepare("INSERT INTO poc_users(id,email,role,salt,password_hash,mfa_hex) VALUES (?,?,?,?,?,?)")
    .bind(id,email.toLowerCase(),role,salt,hash,secret).run();
  return id;
}
export async function signIn(db,email,password,code,now=Math.floor(Date.now()/1000)) {
  if(typeof email!=="string"||email.length>254)return null;
  email=email.trim().toLowerCase();
  const user=await db.prepare("SELECT * FROM poc_users WHERE email=? AND active=1").bind(email).first();
  // Unknown email does not create unbounded rate-limit records.
  if(!user)return null;
  const bucket=await digest(email);
  await db.prepare("INSERT INTO poc_login_limits(bucket,started,count) VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN started<? THEN 1 ELSE count+1 END,started=CASE WHEN started<? THEN excluded.started ELSE started END")
    .bind(bucket,now,now-900,now-900).run();
  if((await db.prepare("SELECT count FROM poc_login_limits WHERE bucket=?").bind(bucket).first()).count>10)return null;
  if(typeof password!=="string"||password.length<12||password.length>128)return null;
  const requiresMfa=user.role==='admin'||!!user.mfa_hex;
  if(requiresMfa&&(!user.mfa_hex||typeof code!=='string'||!/^\d{6}$/.test(code)))return null;
  if(!equal(await passwordHash(password,user.salt),user.password_hash))return null;
  let accepted=requiresMfa?-1:user.last_step+1;
  for(const step of requiresMfa?[Math.floor(now/30),Math.floor(now/30)-1,Math.floor(now/30)+1]:[]){
    if(step>user.last_step&&equal(await totp(user.mfa_hex,step),code)){accepted=step;break;}
  }
  if(accepted<0)return null;
  const token=randomToken(),csrf=randomToken(),hash=await digest(token);
  // Conditional update plus conditional insert are atomic in a D1 batch.
  const results=await db.batch([
    db.prepare("UPDATE poc_users SET last_step=? WHERE id=? AND active=1 AND last_step<?").bind(accepted,user.id,accepted),
    db.prepare("INSERT INTO poc_sessions(hash,user_id,csrf,expires) SELECT ?,?,?,? WHERE changes()=1").bind(hash,user.id,csrf,now+28800)
  ]);
  return results[1].meta.changes===1?{token,csrf}:null;
}
export async function sessionUser(db,token,now=Math.floor(Date.now()/1000)){
  if(typeof token!=="string"||!/^[a-f0-9]{64}$/.test(token))return null;
  return db.prepare("SELECT u.id,u.email,u.role,s.csrf FROM poc_sessions s JOIN poc_users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>? AND u.active=1")
    .bind(await digest(token),now).first();
}
export function canWrite(user,csrf){return !!user&&["admin","write"].includes(user.role)&&equal(user.csrf,csrf);}
export async function revokeUser(db,id){
  await db.batch([
    db.prepare("UPDATE poc_users SET active=0 WHERE id=? AND role!='admin'").bind(id),
    db.prepare("DELETE FROM poc_sessions WHERE user_id IN (SELECT id FROM poc_users WHERE id=? AND role!='admin')").bind(id)
  ]);
}
