import {randomToken,digest,passwordHash,totp,canWrite} from './auth.mjs';

export const invitationSchema = `CREATE TABLE IF NOT EXISTS poc_invitations (
  email TEXT PRIMARY KEY COLLATE NOCASE, token_hash TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK(role IN ('admin','read','write')),
  expires INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0, mfa_hex TEXT NOT NULL
)`;
export async function initializeInvitations(db){await db.prepare(invitationSchema).run();}
const nowSeconds=()=>Math.floor(Date.now()/1000);
const validToken=token=>typeof token==='string'&&/^[a-f0-9]{64}$/.test(token);
export const base32=hex=>{
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits=0,value=0,result='';
  for(const byte of hex.match(/../g).map(x=>parseInt(x,16))){value=(value<<8)|byte;bits+=8;while(bits>=5){result+=alphabet[(value>>>(bits-5))&31];bits-=5;}}
  if(bits)result+=alphabet[(value<<(5-bits))&31];return result;
};
// Called by authenticated admin HTTP route, or a private bootstrap tool only.
export async function createInvitation(db,actor,csrf,email,role,now=nowSeconds()){
  if(actor?.role!=='admin'||!canWrite(actor,csrf))throw new Error('Administrator access required');
  if(typeof email!=='string'||email.length>254||!/^\S+@\S+\.\S+$/.test(email.trim())||!['read','write'].includes(role))throw new Error('Invalid email or role');
  return issueInvitation(db,email.trim().toLowerCase(),role,now);
}
async function issueInvitation(db,email,role,now){
  if(await db.prepare('SELECT id FROM poc_users WHERE email=?').bind(email).first())throw new Error('This user already has an account');
  if((await db.prepare('SELECT COUNT(*) AS n FROM poc_invitations').first()).n>=500 && !await db.prepare('SELECT email FROM poc_invitations WHERE email=?').bind(email).first())throw new Error('Test invitation limit reached');
  const token=randomToken(),secret=randomToken().slice(0,40);
  await db.prepare(`INSERT INTO poc_invitations(email,token_hash,role,expires,mfa_hex) VALUES (?,?,?,?,?)
    ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash,role=excluded.role,expires=excluded.expires,mfa_hex=excluded.mfa_hex,consumed=0,attempts=0`)
    .bind(email,await digest(token),role,now+86400,secret).run();
  return {token,email,expires:now+86400};
}
// Never exposed as a public HTTP route. Bootstrap cannot replace an existing admin.
export async function bootstrapAdmin(db,now=nowSeconds()){
  if(await db.prepare("SELECT id FROM poc_users WHERE role='admin'").first())throw new Error('Administrator already enrolled');
  return issueInvitation(db,'vamsi@datafoldit.com','admin',now);
}
export async function requestInvitationEmail(db,email,deliver,now=nowSeconds()){
  if(typeof email!=='string'||email.length>254)return;
  email=email.trim().toLowerCase();
  const row=await db.prepare('SELECT role FROM poc_invitations WHERE email=? AND consumed=0').bind(email).first();
  if(!row||await db.prepare('SELECT id FROM poc_users WHERE email=?').bind(email).first())return;
  const bucket='mail:'+await digest(email);
  await db.prepare('INSERT INTO poc_login_limits(bucket,started,count) VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN started<? THEN 1 ELSE count+1 END,started=CASE WHEN started<? THEN excluded.started ELSE started END').bind(bucket,now,now-3600,now-3600).run();
  const limit=await db.prepare('SELECT count FROM poc_login_limits WHERE bucket=?').bind(bucket).first();
  if(limit.count>5)return;
  const result=await issueInvitation(db,email,row.role,now);
  await deliver(result.email,result.token);
}
async function invitation(db,token,now){
  if(!validToken(token))return null;
  return db.prepare('SELECT * FROM poc_invitations WHERE token_hash=? AND expires>? AND consumed=0 AND attempts<10').bind(await digest(token),now).first();
}
export async function beginEnrollment(db,token,now=nowSeconds(),issuer='DataFoldIT Test'){
  const row=await invitation(db,token,now);if(!row)return null;
  return {email:row.email,mfaRequired:row.role==='admin',otpauth:'otpauth://totp/'+encodeURIComponent(issuer+':'+row.email)+'?secret='+base32(row.mfa_hex)+'&issuer='+encodeURIComponent(issuer)+'&algorithm=SHA1&digits=6&period=30'};
}
export async function acceptInvitation(db,token,password,code,now=nowSeconds(),skipMfa=false){
  const row=await invitation(db,token,now);if(!row)return false;
  await db.prepare('UPDATE poc_invitations SET attempts=attempts+1 WHERE token_hash=?').bind(row.token_hash).run();
  if(skipMfa&&row.role==='admin')return false;
  if(typeof password!=='string'||password.length<12||password.length>128)return false;
  if(!skipMfa&&(typeof code!=='string'||!/^\d{6}$/.test(code)))return false;
  let step=skipMfa?0:-1;
  for(const n of skipMfa?[]:[Math.floor(now/30),Math.floor(now/30)-1,Math.floor(now/30)+1])if(await totp(row.mfa_hex,n)===code){step=n;break;}
  if(step<0)return false;
  const salt=randomToken(),hash=await passwordHash(password,salt),id=crypto.randomUUID();
  const results=await db.batch([
    db.prepare(`INSERT INTO poc_users(id,email,role,salt,password_hash,mfa_hex,last_step)
      SELECT ?,email,role,?,?,CASE WHEN ?=1 AND role!='admin' THEN '' ELSE mfa_hex END,? FROM poc_invitations WHERE token_hash=? AND consumed=0 AND expires>? AND attempts<=10
      AND NOT EXISTS(SELECT 1 FROM poc_users WHERE email=poc_invitations.email)`)
      .bind(id,salt,hash,skipMfa?1:0,step,row.token_hash,now),
    db.prepare('UPDATE poc_invitations SET consumed=1,mfa_hex=? WHERE token_hash=? AND changes()=1').bind('',row.token_hash)
  ]);
  return results[0].meta.changes===1;
}
