// Local Qwen handles Korean directly. Never send chat text to a third-party translator.
export function handleTranslation(message) {
    return String(message || '');
}

export function handleEnglishTranslation(message) {
    return String(message || '');
}
