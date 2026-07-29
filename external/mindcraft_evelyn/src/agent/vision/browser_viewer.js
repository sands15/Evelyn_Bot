import settings from '../settings.js';

export async function addBrowserViewer(bot, count_id) {
    if (!settings.render_bot_view) return;
    const prismarineViewer = await import('prismarine-viewer');
    const mineflayerViewer = prismarineViewer.default?.mineflayer || prismarineViewer.mineflayer;
    mineflayerViewer(bot, { port: 3000 + count_id, firstPerson: true });
}
