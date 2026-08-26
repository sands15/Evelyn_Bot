import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

import customPvpModule from '@nxg-org/mineflayer-custom-pvp';


const customPvp = customPvpModule?.default || customPvpModule;
const require = createRequire(import.meta.url);
const { SwordPvp } = require(
    '@nxg-org/mineflayer-custom-pvp/lib/sword/swordpvp.js'
);

if (typeof customPvp !== 'function') {
    throw new Error('mindcraft_custom_pvp_plugin_unavailable');
}
if (
    typeof SwordPvp?.prototype?.attack !== 'function' ||
    typeof SwordPvp?.prototype?.stop !== 'function'
) {
    throw new Error('mindcraft_custom_pvp_compatibility_contract_failed');
}

const mcdataSource = readFileSync(new URL('../src/utils/mcdata.js', import.meta.url), 'utf8');
if (!mcdataSource.includes("bot.once('inject_allowed', () => { bot.pvp = bot.swordpvp; })")) {
    throw new Error('mindcraft_custom_pvp_alias_lifecycle_failed');
}

console.log('mindcraft-combat-runtime-ok');
