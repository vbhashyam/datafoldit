(() => {
 const key='datafoldit-test-theme';
 let theme='dark';
 try{if(localStorage.getItem(key)==='light')theme='light';}catch{}
 function apply(value){
  theme=value==='light'?'light':'dark';document.documentElement.dataset.theme=theme;
  const button=document.getElementById('theme-toggle');
  if(button){button.textContent=theme==='dark'?'☀ Light mode':'☾ Dark mode';button.setAttribute('aria-label','Switch to '+(theme==='dark'?'light':'dark')+' mode');button.title=button.getAttribute('aria-label');}
 }
 apply(theme);
 function init(){apply(theme);const button=document.getElementById('theme-toggle');if(button)button.onclick=()=>{apply(theme==='dark'?'light':'dark');try{localStorage.setItem(key,theme);}catch{}};}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
 window.addEventListener('storage',event=>{if(event.key===key)apply(event.newValue);});
})();
