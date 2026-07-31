import { ESLint } from 'eslint';


const eslint = new ESLint();
const valid = await eslint.lintText(`
export async function main(bot) {
    await Promise.resolve(bot);
}
`);
const invalid = await eslint.lintText(`
async function helper() {
    await Promise.resolve();
}
export async function main(bot) {
    helper();
    await Promise.resolve(bot);
}
`);

const validMessages = valid.flatMap((result) => result.messages);
const invalidRuleIds = invalid
    .flatMap((result) => result.messages)
    .map((message) => message.ruleId);

if (validMessages.length !== 0) {
    throw new Error('mindcraft_runtime_lint_valid_contract_failed');
}
if (
    !invalidRuleIds.includes(
        'no-floating-promise/no-floating-promise'
    )
) {
    throw new Error(
        'mindcraft_runtime_lint_floating_promise_not_rejected'
    );
}

console.log('mindcraft-runtime-lint-ok');
