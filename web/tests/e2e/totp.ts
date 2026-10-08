// TOTP codes (RFC 6238: SHA-1, 6 digits, 30-second steps) with Web Crypto, for the smoke test.

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function base32(secret: string): Uint8Array<ArrayBuffer> {
	const bits = [...secret.replace(/=+$/, '').toUpperCase()]
		.map((char) => ALPHABET.indexOf(char).toString(2).padStart(5, '0'))
		.join('');
	const bytes = new Uint8Array(Math.floor(bits.length / 8));
	for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
	return bytes;
}

/** The time step of `at` (milliseconds). */
export function step(at: number = Date.now()): number {
	return Math.floor(at / 1000 / 30);
}

/** The code of `secret` (base32) for time step `counter`. */
export async function code(secret: string, counter: number): Promise<string> {
	const key = await crypto.subtle.importKey(
		'raw',
		base32(secret),
		{ name: 'HMAC', hash: 'SHA-1' },
		false,
		['sign']
	);
	const message = new DataView(new ArrayBuffer(8));
	message.setBigUint64(0, BigInt(counter));
	const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, message.buffer));
	const offset = mac[19] & 0x0f;
	const value =
		(((mac[offset] & 0x7f) << 24) |
			(mac[offset + 1] << 16) |
			(mac[offset + 2] << 8) |
			mac[offset + 3]) %
		1_000_000;
	return value.toString().padStart(6, '0');
}
