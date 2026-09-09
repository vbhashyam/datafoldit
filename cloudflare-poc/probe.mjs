// Local workerd compatibility probe. Synthetic data only; never deploy this.
import { Miniflare } from "miniflare";
const runtime = new Miniflare({
  modules: true,
  compatibilityDate: "2026-08-06",
  d1Databases: ["DB"],
  r2Buckets: ["FILES"],
  script: `
    export default {
      async fetch(request, env) {
        const results = {};
        const key = await crypto.subtle.importKey(
          "raw", new TextEncoder().encode("synthetic-test-password-only"),
          "PBKDF2", false, ["deriveBits"]
        );
        try {
          const start = Date.now();
          await crypto.subtle.deriveBits({
            name: "PBKDF2", hash: "SHA-256",
            salt: new Uint8Array(16).fill(7), iterations: 600000
          }, key, 256);
          results.password = {supported: true, wallMilliseconds: Date.now()-start};
        } catch (error) {
          results.password = {supported:false, error:error.message};
        }
        const hmacKey = await crypto.subtle.importKey(
          "raw", new TextEncoder().encode("12345678901234567890"),
          {name:"HMAC",hash:"SHA-1"}, false, ["sign"]
        );
        const counter = new Uint8Array(8);
        counter[7]=1;
        const hmac = new Uint8Array(await crypto.subtle.sign("HMAC",hmacKey,counter));
        const offset=hmac[hmac.length-1]&15;
        const number=((hmac[offset]&127)<<24)|(hmac[offset+1]<<16)|(hmac[offset+2]<<8)|hmac[offset+3];
        results.mfa = String(number%1000000).padStart(6,"0")==="287082";
        await env.DB.prepare("CREATE TABLE sample_transactions (id INTEGER PRIMARY KEY, amount_cents INTEGER NOT NULL)").run();
        await env.DB.prepare("INSERT INTO sample_transactions(amount_cents) VALUES (?)").bind(12345).run();
        results.database = (await env.DB.prepare("SELECT amount_cents FROM sample_transactions").first()).amount_cents===12345;
        await env.FILES.put("synthetic.txt","Synthetic attachment — no personal data.");
        results.upload = (await (await env.FILES.get("synthetic.txt")).text()).startsWith("Synthetic attachment");
        return Response.json(results);
      }
    };
  `
});
try {
  const response = await runtime.dispatchFetch("http://localhost/probe");
  console.log(JSON.stringify(await response.json(),null,2));
} finally {
  await runtime.dispose();
}
