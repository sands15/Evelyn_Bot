const Observation = require("./base.js").Observation;

class onChat extends Observation {
    constructor(bot) {
        super(bot);
        this.name = "onChat";
        this.obs = [];
        bot.on("chatEvent", (username, message) => {
            // Save entity status to local variable
            if (message.startsWith("/")) {
                return;
            }

            this.obs.push(message);
            this.bot.event(this.name);
        });
    }

    observe() {
        const result = this.obs.join("\n");
        this.obs = [];
        return result;
    }
}

module.exports = onChat;
