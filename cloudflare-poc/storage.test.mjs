import assert from "node:assert/strict";
import {Miniflare} from "miniflare";
import {initialize,saveFile,loadFile,deleteFile,MAX_FILE_BYTES} from "./free-storage.mjs";

const runtime=new Miniflare({
  modules:true, compatibilityDate:"2026-08-06", d1Databases:["DB"],
  script:"export default {fetch(){return new Response('Local test only')}}"
});
try{
  const db=await runtime.getD1Database("DB");
  await initialize(db);
  const data=new Uint8Array(MAX_FILE_BYTES);
  for(let i=0;i<data.length;i++) data[i]=i%251;
  const id=await saveFile(db,"sample-owner","sample-bulk.pdf",data);
  assert.deepEqual((await loadFile(db,"sample-owner",id)).bytes,data);
  assert.equal(await loadFile(db,"different-owner",id),null);
  await deleteFile(db,"different-owner",id);
  assert.ok(await loadFile(db,"sample-owner",id));
  await assert.rejects(saveFile(db,"sample-owner","too-large.pdf",new Uint8Array(MAX_FILE_BYTES+1)));
  await assert.rejects(saveFile(db,"sample-owner","empty.pdf",new Uint8Array()));
  await deleteFile(db,"sample-owner",id);
  assert.equal(await loadFile(db,"sample-owner",id),null);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM test_file_chunks").first()).n,0);
  // Metadata-only fixtures exercise the atomic quota trigger without large memory use.
  for(let n=0;n<20;n++){
    await db.prepare("INSERT INTO test_files VALUES (?,?,?,?)").bind("quota-"+n,"sample-owner","quota-fixture",MAX_FILE_BYTES).run();
  }
  await assert.rejects(saveFile(db,"sample-owner","over-quota.txt",new Uint8Array([1])));
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM test_files").first()).n,20);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM test_file_chunks").first()).n,0);
  console.log("PASS: 5 MiB round-trip, owner isolation, unauthorized-delete protection, size limits, cascade deletion, atomic quota rejection.");
}finally{
  await runtime.dispose();
}
