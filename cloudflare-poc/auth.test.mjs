import assert from "node:assert/strict";
import {Miniflare} from "miniflare";
import {initializeAuth,provisionUser,signIn,sessionUser,totp,canWrite,revokeUser} from "./auth.mjs";
const runtime=new Miniflare({modules:true,compatibilityDate:"2026-08-06",d1Databases:["DB"],script:"export default {fetch(){return new Response('Local only')}}"});
try{
  const db=await runtime.getD1Database("DB");await initializeAuth(db);
  const secret="3132333435363738393031323334353637383930",password="Synthetic-test-password-123",now=1800000000;
  const reader=await provisionUser(db,"reader@example.com",password,"read",secret);
  const writer=await provisionUser(db,"writer@example.com",password,"write",secret);
  const code=await totp(secret,Math.floor(now/30));
  assert.equal(await signIn(db,"reader@example.com","Incorrect-password",code,now),null);
  assert.equal(await signIn(db,"reader@example.com",password,"000000",now),null);
  const readSession=await signIn(db,"reader@example.com",password,code,now);assert.ok(readSession);
  assert.equal(await signIn(db,"reader@example.com",password,code,now),null,"MFA replay must fail");
  const readUser=await sessionUser(db,readSession.token,now);
  assert.equal(readUser.id,reader);assert.equal(canWrite(readUser,readSession.csrf),false);
  assert.equal(await sessionUser(db,readSession.token,now+28801),null);
  const writeSession=await signIn(db,"writer@example.com",password,code,now);
  const writeUser=await sessionUser(db,writeSession.token,now);
  assert.equal(canWrite(writeUser,writeSession.csrf),true);assert.equal(canWrite(writeUser,"forged"),false);
  assert.equal(canWrite(null,"forged"),false);
  await revokeUser(db,writer);assert.equal(await sessionUser(db,writeSession.token,now),null);
  for(let i=0;i<11;i++)await signIn(db,"reader@example.com","Incorrect-password",code,now);
  assert.equal(await signIn(db,"reader@example.com",password,await totp(secret,Math.floor(now/30)+1),now+30),null);
  console.log("PASS: password + MFA, replay rejection, read/write permissions, CSRF validation, expiry, revocation, login throttling.");
}finally{await runtime.dispose();}
