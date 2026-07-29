import { Vec3 } from 'vec3';
import fs from 'fs';

export class VisionInterpreter {
    constructor(agent, allow_vision) {
        this.agent = agent;
        this.allow_vision = allow_vision;
        this.fp = './bots/' + agent.name + '/screenshots/';
        this.cameraPromise = allow_vision
            ? import('./camera.js').then(({ Camera }) => new Camera(agent.bot, this.fp))
            : null;
    }

    async capture() {
        if (!this.cameraPromise) throw new Error('Vision is disabled.');
        const camera = await this.cameraPromise;
        return camera.capture();
    }

    async lookAtPlayer(player_name, direction) {
        if (!this.allow_vision || !this.agent.prompter.vision_model.sendVisionRequest) {
            return 'Vision is disabled. Use other methods to describe the environment.';
        }
        let result = '';
        const bot = this.agent.bot;
        const player = bot.players[player_name]?.entity;
        if (!player) return `Could not find player ${player_name}`;
        let filename;
        if (direction === 'with') {
            await bot.look(player.yaw, player.pitch);
            result = `Looking in the same direction as ${player_name}\n`;
            filename = await this.capture();
        } else {
            await bot.lookAt(new Vec3(player.position.x, player.position.y + player.height, player.position.z));
            result = `Looking at player ${player_name}\n`;
            filename = await this.capture();
        }
        return result + `Image analysis: "${await this.analyzeImage(filename)}"`;
    }

    async lookAtPosition(x, y, z) {
        if (!this.allow_vision || !this.agent.prompter.vision_model.sendVisionRequest) {
            return 'Vision is disabled. Use other methods to describe the environment.';
        }
        const bot = this.agent.bot;
        await bot.lookAt(new Vec3(x, y + 2, z));
        const filename = await this.capture();
        return `Looking at coordinate ${x}, ${y}, ${z}\nImage analysis: "${await this.analyzeImage(filename)}"`;
    }

    getCenterBlockInfo() {
        const targetBlock = this.agent.bot.blockAtCursor(128);
        return targetBlock
            ? `Block at center view: ${targetBlock.name} at (${targetBlock.position.x}, ${targetBlock.position.y}, ${targetBlock.position.z})`
            : 'No block in center view';
    }

    async analyzeImage(filename) {
        try {
            const imageBuffer = fs.readFileSync(`${this.fp}/${filename}.jpg`);
            const messages = this.agent.history.getHistory();
            const result = await this.agent.prompter.promptVision(messages, imageBuffer);
            return result + `\n${this.getCenterBlockInfo()}`;
        } catch (error) {
            console.warn('Error reading image:', error);
            return `Error reading image: ${error.message}`;
        }
    }
}
