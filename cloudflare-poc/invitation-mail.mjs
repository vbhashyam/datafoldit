const ORIGIN='https://datafoldit-test-poc.vamsibh07.workers.dev';
const emailValid=email=>typeof email==='string'&&/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email)&&email.length<=254;
export async function smtpConversation(socket,config,email,link,production=false){
  const encoder=new TextEncoder(),decoder=new TextDecoder(),reader=socket.readable.getReader(),writer=socket.writable.getWriter();
  let buffer='',total=0;
  async function response(expected){
    let code;
    for(let i=0;i<100;i++){
      while(!buffer.includes('\r\n')){const {done,value}=await reader.read();if(done)throw new Error('Mail connection closed');total+=value.length;if(total>65536)throw new Error('Mail response limit');buffer+=decoder.decode(value,{stream:true});}
      const end=buffer.indexOf('\r\n'),line=buffer.slice(0,end);buffer=buffer.slice(end+2);
      if(!/^\d{3}[ -]/.test(line))throw new Error('Invalid mail response');
      if(code&&code!==line.slice(0,3))throw new Error('Invalid multiline response');code=line.slice(0,3);
      if(line[3]===' '){if(!expected.includes(Number(code)))throw new Error('Mail server rejected request');return;}
    }throw new Error('Mail response limit');
  }
  async function command(value,codes){await writer.write(encoder.encode(value+'\r\n'));await response(codes);}
  await response([220]);await command('EHLO datafoldit-test-poc.vamsibh07.workers.dev',[250]);
  await command('AUTH LOGIN',[334]);await command(btoa(config.sender),[334]);await command(btoa(config.password),[235]);
  await command('MAIL FROM:<'+config.sender+'>',[250]);await command('RCPT TO:<'+email+'>',[250,251]);await command('DATA',[354]);
  const body='You have been approved for the DataFoldIT '+(production?'PRODUCTION':'TEST')+' workspace.\r\n\r\nVerify your email and create your password using this private link:\r\n'+link+'\r\n\r\nThe link expires in 24 hours and can be used once.\r\nAdmin MFA is required. Employee MFA is optional.\r\nDo not forward this email. If unexpected, contact vamsi@datafoldit.com.\r\n';
  const encoded=btoa(body).match(/.{1,76}/g).join('\r\n');
  const workspace=production?'Production':'Test';
  await command('From: DataFoldIT '+workspace+' <'+config.sender+'>\r\nTo: <'+email+'>\r\nSubject: Verify your email - DataFoldIT '+workspace.toUpperCase()+'\r\nDate: '+new Date().toUTCString()+'\r\nMessage-ID: <'+crypto.randomUUID()+'@datafoldit.com>\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n'+encoded+'\r\n.',[250]);
  // Acceptance above is authoritative; a failed QUIT must not cause duplicate sends.
  try{await command('QUIT',[221]);}catch{}
  return true;
}
export async function sendInvitationMail(env,email,token){
  if(!emailValid(email)||!/^[a-f0-9]{64}$/.test(token))throw new Error('Invalid invitation');
  const production=env.DEPLOYMENT_ENV==='production';
  const origin=production?env.PUBLIC_ORIGIN:ORIGIN;
  if(production&&!['https://www.accounts.datafoldit.com','https://datafoldit-production.vamsibh07.workers.dev'].includes(origin))throw new Error('Production invitation origin is not configured');
  const link=origin+'/accept#token='+token;
  if(env.MAILER){const r=await env.MAILER.fetch('https://mailer.test/send',{method:'POST',body:JSON.stringify({email,link})});if(!r.ok)throw new Error('Mail unavailable');return;}
  const config=JSON.parse(env.SMTP_CONFIG||'null');
  const hosts=['smtppro.zoho.com','smtp.zoho.com','smtppro.zoho.eu','smtp.zoho.eu','smtppro.zoho.in','smtp.zoho.in'];
  if(!config||!hosts.includes(config.host)||config.sender!=='vamsi@datafoldit.com'||typeof config.password!=='string')throw new Error('Email not configured');
  const {connect}=await import('cloudflare:sockets');
  const socket=connect({hostname:config.host,port:465},{secureTransport:'on'});
  socket.closed.catch(()=>{});
  let timer;
  try{await Promise.race([smtpConversation(socket,config,email,link,production),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Mail timed out')),20000);})]);}
  finally{clearTimeout(timer);await socket.close().catch(()=>{});}
}
