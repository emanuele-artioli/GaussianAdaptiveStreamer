class ThroughputABR {
    constructor() {
        this._initialized = false;

        this.minProfile = 0;
        this.maxProfile = 3;
        this.profile = 3;

        this.lastReceivedBytes = 0;
        this.lastBitrate = 0;
        this.previousEstimatedTimestep = 0;

        this.goodStreak = 0;
        this.badStreak = 0;
        this.upgradeRequiredStreak = 20;
        this.upgradeBoundaryMs = 15;
        this.downgradeRequiredStreak = 7;
        this.downgradeBoundaryMs = 50;
        
        this.lastProcessingTimeMs = 0;
        this.lastFinishProcessingTime = 0;
        this.processingTimeGoodStreak = 0;
        this.processingTimeGoodStreakBoundary = 15;

        this.estimateLatency = 0;

        this.lastSendTimestemp = 0;
        this.lastReceiveTimestemp = 0;
        this.lastTimeDelta = 0;
        this.accumulateDelta = 0;
        this.timeIncreaseStreak = 0;
        this.timeDeceraseStreak = 0;
        this.increaseStreakBoundary = 6;
        this.decreaseStreakBoundary = 15;

        this.renderMs = 0;

        this._timer = null;
        this.fps = 60;

        this.rx = 800;
        this.ry = 600;
        this.bpp = 0;

        setInterval(this.estimateBitrate.bind(this), 500);
    }

    _ewma(oldVal, newVal, alpha = 0.2) {
        if (oldVal === 0) return newVal;
        return alpha * newVal + (1 - alpha) * oldVal;
    }

    pickProfile() {
        this.shouldChangeABRLevel();
        return this.profile;
    }

    startRequest() { 
        // No-op
    }

    endRequest(contentLengthBytes = 0, rx = 0, ry = 0, renderMs = NaN) {
        // No-op
    }

    addReceiveBytes(size) {
        this.lastReceivedBytes += size;
    }

    estimateBitrate() {
        if (this.previousEstimatedTimestep == 0) {
            this.previousEstimatedTimestep = performance.now();
            this.lastBitrate = 0;
            return;
        }

        const curTime = performance.now();
        const elapsed = (curTime - this.previousEstimatedTimestep) / 1000;
        const newThroughput = this.lastReceivedBytes / elapsed;
        this.previousEstimatedTimestep = curTime;

        this.lastBitrate = this._ewma(this.lastBitrate, newThroughput);
        this.lastReceivedBytes = 0;
    }

    calcFrameTimeDiff() {
        const now = performance.now();
        const diff = now - this.lastFinishProcessingTime;
        this.lastFinishProcessingTime = now;
    }

    // calcThroughput(size, time) {
        // this.lastFinishProcessingTime = time;
        // console.log("Throughput:", size / (time / 1000), size, time);
    // }

    updateProcessingTime(time) {
        this.lastFinishProcessingTime = time;
        if (this.lastFinishProcessingTime * 3.3 < 1 / this.fps * 1000) {
            this.processingTimeGoodStreak++;
            // console.log(this.lastFinishProcessingTime);
        }
        else {
            this.processingTimeGoodStreak = 0;
        }
    }

    updateChunkCompleteTime(sendTime, compTime) {
        if (this.lastSendTimestemp == 0 || this.lastReceiveTimestemp == 0) {
            this.lastSendTimestemp = sendTime;
            this.lastReceiveTimestemp = compTime;
            return;
        }

        const deltaSendTime = sendTime - this.lastSendTimestemp;
        const deltaRecvTime = compTime - this.lastReceiveTimestemp;
        const delta = deltaRecvTime - deltaSendTime;

        if (delta > 0) {
            this.timeIncreaseStreak++;
            this.timeDeceraseStreak = 0;
        }
        else {
            this.timeDeceraseStreak++;
            this.timeIncreaseStreak = 0;
        }

        this.lastTimeDelta = delta;
        this.lastSendTimestemp = sendTime;
        this.lastReceiveTimestemp = compTime;
    }

    updateWithEstimatedRTT(rtt) {
        if (rtt > this.downgradeBoundaryMs) {
            this.badStreak++;
        }
        else {
            this.badStreak = 0;
        }

        if (rtt < this.upgradeBoundaryMs) {
            this.goodStreak++;
        }
        else {
            this.goodStreak = 0;
        }
    }

    shouldChangeABRLevel() {
        if (this.badStreak > this.downgradeRequiredStreak) {
            this.downgrade();
            this.badStreak = 0;
            this.goodStreak = 0;
            this.timeIncreaseStreak = 0;
            console.log("Downgrade based on latency");
            return;
        }

        if (this.timeIncreaseStreak > this.increaseStreakBoundary) {
            this.downgrade();
            this.badStreak = 0;
            this.goodStreak = 0;
            this.timeIncreaseStreak = 0;
            console.log("Downgrade based on delta");
            return;
        }

        if (this.goodStreak > this.upgradeRequiredStreak) {
            this.upgrade();
            this.goodStreak = 0;
            console.log("Upgrade based on latency");
            return;
        }
    }

    downgrade() {
        this.accumulateDelta = 0;
        if (this.profile < 3) {
            this.profile++;
            return;
        }

        if (this.profile == 3) {
            this.fps = 30;
        }
    }

    upgrade() {
        if (this.processingTimeGoodStreak < this.processingTimeGoodStreakBoundary) {
            console.log("not safe for upgrade");
            return;
        }

        this.accumulateDelta = 0;
        if (this.profile == 0) {
            return;
        }

        if (this.profile == 3 && this.fps < 60) {
            this.fps = 60;
            return;
        }

        if (this.profile > 0) {
            this.profile--;
        }
    }

    reset() {
        this._initialized = false;

        this.minProfile = 0;
        this.maxProfile = 3;
        this.profile = 3;

        this.lastReceivedBytes = 0;
        this.lastBitrate = 0;
        this.previousEstimatedTimestep = 0;

        this.goodStreak = 0;
        this.badStreak = 0;
        this.upgradeRequiredStreak = 20;
        this.upgradeBoundaryMs = 15;
        this.downgradeRequiredStreak = 7;
        this.downgradeBoundaryMs = 50;
        
        this.lastProcessingTimeMs = 0;
        this.lastFinishProcessingTime = 0;
        this.processingTimeGoodStreak = 0;
        this.processingTimeGoodStreakBoundary = 15;

        this.estimateLatency = 0;

        this.lastSendTimestemp = 0;
        this.lastReceiveTimestemp = 0;
        this.lastTimeDelta = 0;
        this.accumulateDelta = 0;
        this.timeIncreaseStreak = 0;
        this.timeDeceraseStreak = 0;
        this.increaseStreakBoundary = 6;
        this.decreaseStreakBoundary = 15;

        this._timer = null;
        this.fps = 60;

        this.rx = 800;
        this.ry = 600;
        this.bpp = 0;
    }
}