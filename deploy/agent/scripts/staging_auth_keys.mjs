import crypto from 'node:crypto';

const input = JSON.parse(await new Promise((resolve, reject) => {
  let value = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { value += chunk; });
  process.stdin.on('end', () => resolve(value));
  process.stdin.on('error', reject);
}));
const jwtSecret = input.jwtSecret;
if (typeof jwtSecret !== 'string' || jwtSecret.length < 32) {
  throw new Error('A strong JWT secret is required');
}

const b64 = (value) => Buffer.from(value).toString('base64url');
const issued = Math.floor(Date.now() / 1000);
const expires = issued + 5 * 365 * 24 * 3600;
const payload = (role) => ({ role, iss: 'supabase', iat: issued, exp: expires });
const hsJwt = (role) => {
  const body = `${b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${b64(JSON.stringify(payload(role)))}`;
  const sig = crypto.createHmac('sha256', jwtSecret).update(body).digest('base64url');
  return `${body}.${sig}`;
};

const { privateKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' });
const privateJwk = privateKey.export({ format: 'jwk' });
const kid = crypto.randomUUID();
const shared = {
  kty: 'EC', kid, use: 'sig', alg: 'ES256', ext: true, crv: privateJwk.crv,
  x: privateJwk.x, y: privateJwk.y,
};
const privateEc = { ...shared, key_ops: ['sign', 'verify'], d: privateJwk.d };
const publicEc = { ...shared, key_ops: ['verify'] };
const oct = { kty: 'oct', k: b64(jwtSecret), alg: 'HS256' };
const esJwt = (role) => {
  const body = `${b64(JSON.stringify({ alg: 'ES256', typ: 'JWT', kid }))}.${b64(JSON.stringify(payload(role)))}`;
  const signature = crypto.sign('SHA256', Buffer.from(body), {
    key: privateKey, dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  return `${body}.${signature}`;
};
const opaque = (prefix) => {
  const intermediate = `${prefix}${crypto.randomBytes(17).toString('base64url').slice(0, 22)}`;
  const checksum = crypto.createHash('sha256')
    .update(`supabase-self-hosted|${intermediate}`).digest('base64url').slice(0, 8);
  return `${intermediate}_${checksum}`;
};
process.stdout.write(JSON.stringify({
  ANON_KEY: hsJwt('anon'),
  SERVICE_ROLE_KEY: hsJwt('service_role'),
  SUPABASE_PUBLISHABLE_KEY: opaque('sb_publishable_'),
  SUPABASE_SECRET_KEY: opaque('sb_secret_'),
  ANON_KEY_ASYMMETRIC: esJwt('anon'),
  SERVICE_ROLE_KEY_ASYMMETRIC: esJwt('service_role'),
  JWT_KEYS: JSON.stringify([privateEc, oct]),
  JWT_JWKS: JSON.stringify({ keys: [publicEc, oct] }),
}));
