// Restricted test attachment storage. No R2 subscription or paid services.
// ownerId must come from a verified server-side session, never request fields.
export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 100 * 1024 * 1024;
const CHUNK_BYTES = 256 * 1024;
export const schema = [
  "CREATE TABLE IF NOT EXISTS test_files (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>0 AND size<=5242880))",
  "CREATE TABLE IF NOT EXISTS test_file_chunks (file_id TEXT NOT NULL REFERENCES test_files(id) ON DELETE CASCADE, position INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(file_id,position))",
  "CREATE INDEX IF NOT EXISTS test_files_owner ON test_files(owner_id)",
  "CREATE TRIGGER IF NOT EXISTS test_file_quota BEFORE INSERT ON test_files BEGIN SELECT CASE WHEN (SELECT COALESCE(SUM(size),0) FROM test_files)+NEW.size>104857600 OR (SELECT COUNT(*) FROM test_files)>=100 THEN RAISE(ABORT,'Test attachment quota reached') END; END"
];

export async function initialize(db) {
  for (const sql of schema) await db.prepare(sql).run();
}

export async function saveFile(db, ownerId, name, bytes) {
  if (!ownerId || typeof ownerId !== "string") throw new Error("Verified owner required");
  if (typeof name !== "string" || !name.trim() || name.length>200 || /[\x00-\x1f]/.test(name)) throw new Error("Invalid filename");
  if (!(bytes instanceof Uint8Array) || bytes.length===0 || bytes.length>MAX_FILE_BYTES) throw new Error("Test uploads must be between 1 byte and 5 MiB");
  const id=crypto.randomUUID();
  const statements=[db.prepare("INSERT INTO test_files (id,owner_id,name,size) VALUES (?,?,?,?)").bind(id,ownerId,name,bytes.length)];
  for(let start=0,position=0;start<bytes.length;start+=CHUNK_BYTES,position++){
    statements.push(db.prepare("INSERT INTO test_file_chunks(file_id,position,data) VALUES (?,?,?)").bind(id,position,bytes.slice(start,start+CHUNK_BYTES)));
  }
  // D1 batch is transactional: failure must not leave partial files or quota use.
  await db.batch(statements);
  return id;
}

export async function loadFile(db, ownerId, id) {
  const file=await db.prepare("SELECT id,name,size FROM test_files WHERE id=? AND owner_id=?").bind(id,ownerId).first();
  if(!file) return null;
  const {results}=await db.prepare("SELECT position,data FROM test_file_chunks WHERE file_id=? ORDER BY position").bind(id).all();
  const output=new Uint8Array(file.size);
  let offset=0;
  for(const [position,part] of results.entries()){
    if(part.position!==position) throw new Error("Incomplete attachment");
    const data=new Uint8Array(part.data);
    output.set(data,offset); offset+=data.length;
  }
  if(offset!==file.size) throw new Error("Incomplete attachment");
  return {...file,bytes:output};
}

export async function deleteFile(db, ownerId, id) {
  return db.prepare("DELETE FROM test_files WHERE id=? AND owner_id=?").bind(id,ownerId).run();
}
