// Literal client code so bundling does not introduce out-of-scope helpers.
export const exportLoaderJs=String.raw`
let exportModulePromise=null;
async function loadExportModule(importer){
 if(!exportModulePromise){
  const load=importer||(url=>import(url));
  exportModulePromise=(async()=>{
   try{return await load('/static/export-ui.js?v=20260909-1');}
   catch(first){
    if(!/fetch|load|import|network/i.test(String(first.message)))throw first;
    try{return await load('/static/export-ui.js?v=20260909-1&retry='+Date.now());}
    catch{throw Error('Could not load Excel export. Check your connection, refresh this page, and try again. No transaction data was changed.');}
   }
  })();
 }
 try{return await exportModulePromise;}catch(error){exportModulePromise=null;throw error;}
}
`;
