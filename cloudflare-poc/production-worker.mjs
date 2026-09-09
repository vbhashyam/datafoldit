// Production phases default to onboarding; only explicit live mode permits business writes.
import application from './worker.mjs';
import {sessionUser} from './auth.mjs';
import {dashboardPage} from './dashboard.mjs';
const allowed=new Set(['/','/login','/accept','/login.js','/enrollment.js','/security','/security.js','/poc.css','/api/access/request','/api/enrollment/begin','/api/enrollment/accept','/api/login','/api/logout','/api/security/begin','/api/security/confirm']);
const secureHeaders={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY'};
export default {
 async fetch(request,env){
  if(env.DEPLOYMENT_ENV!=='production')return new Response('Production configuration required',{status:503});
  const url=new URL(request.url);
  const review=env.RELEASE_PHASE==='review';
  const live=env.RELEASE_PHASE==='live';
  if(!['https://datafoldit-production.vamsibh07.workers.dev','https://www.accounts.datafoldit.com'].includes(url.origin))return new Response('Invalid host',{status:403});
  if(url.pathname==='/healthz')return Response.json({status:'ok',environment:'production',phase:live?'live':review?'read-only-review':'admin-onboarding',businessWritesEnabled:live},{headers:secureHeaders});
  const livePath=live&&(new Set(['/dashboard.js','/api/records','/api/attachment-links','/api/import','/api/operational-backup','/admin','/admin.js','/api/admin/users','/api/admin/invitations','/api/admin/user-access']).has(url.pathname)||url.pathname.startsWith('/api/import-files/'));
  const reviewPath=review&&(new Set(['/dashboard.js','/api/records','/api/attachment-links']).has(url.pathname)||url.pathname.startsWith('/api/import-files/'));
  const reviewAdminPath=review&&new Set(['/admin','/admin.js','/api/admin/users','/api/admin/invitations','/api/admin/user-access']).has(url.pathname);
  if(reviewPath&&request.method!=='GET')return Response.json({error:'Read-only production review. Changes are disabled until cutover.'},{status:403,headers:secureHeaders});
  if(!allowed.has(url.pathname)&&!url.pathname.startsWith('/static/')&&!reviewPath&&!reviewAdminPath&&!livePath)return Response.json({error:'This endpoint is unavailable.'},{status:503,headers:secureHeaders});
  if(url.pathname==='/'&&request.method==='GET'){
   const token=request.headers.get('Cookie')?.match(/(?:^|;\s*)__Host-dfit_poc=([^;]+)/)?.[1];
   const user=token?await sessionUser(env.DB,token):null;
   if(user){
    if(!review&&!live&&user.role!=='admin')return new Response('Administrator onboarding only',{status:403,headers:secureHeaders});
    if(live){
     const body=dashboardPage(user).replace('Test workspace','Production workspace').replace('TEST WORKSPACE','PRODUCTION').replace('Isolated employee testing. Use synthetic data only. Production data has not been copied.','Live production workspace. The old dashboard at accounts.datafoldit.com is retained read-only for comparison.').replace('<body>','<link rel="stylesheet" href="/static/financial-views.css"><body>');
     return new Response(body,{headers:{...secureHeaders,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}});
    }
    if(review){
     const body=dashboardPage({...user,role:'read'}).replace('Test workspace','Production review').replace('TEST WORKSPACE','PRODUCTION REVIEW').replace('Isolated employee testing. Use synthetic data only. Production data has not been copied.','Read-only production snapshot for comparison. Edits are disabled for everyone. Continue live work at accounts.datafoldit.com until cutover.').replace('</aside>',(user.role==='admin'?'<a href="/admin">Manage users</a>':'')+'</aside>').replace('<body>','<link rel="stylesheet" href="/static/financial-views.css"><body>');
     return new Response(body,{headers:{...secureHeaders,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}});
    }
    return new Response('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DataFoldIT Production Setup</title><link rel="stylesheet" href="/poc.css"><main><h1>Welcome Vamsi!</h1><p>Your production administrator login is working.</p><p>Migration is awaiting final verification. This is not yet the live business dashboard. Your existing production dashboard remains unchanged.</p><p>Do not invite employees to this address yet.</p><a href="https://accounts.datafoldit.com/">Open current production</a></main></html>',{headers:{...secureHeaders,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'"}});
   }
  }
  const response=await application.fetch(request,env);
  if(url.pathname==='/dashboard.js'&&response.ok){
   const body=(await response.text()).replaceAll('Delete this test transaction?','Delete this transaction?').replaceAll('Add test records to begin.','Add records to begin.').replaceAll('all recorded test data','all recorded production data');
   return new Response(body,{status:response.status,headers:response.headers});
  }
  if(response.headers.get('Content-Type')?.includes('text/html')){
   let body=(await response.text()).replaceAll('DataFoldIT Test','DataFoldIT Production').replaceAll('Test workspace',live?'Production workspace':review?'Production review':'Production setup');
   if(review&&url.pathname==='/admin')body=body.replace('<h1>User access</h1>','<h1>User access</h1><p><strong>Read-only review:</strong> Invite employees to review real production data. Transaction edits and uploads are disabled for everyone until cutover, including users assigned read/write access. Only invite people authorized to see these records.</p>');
   return new Response(body,{status:response.status,headers:response.headers});
  }
  return response;
 }
};
