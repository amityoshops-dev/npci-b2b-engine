module.exports = {
  apps: [{
    name: "npci-gateway",
    script: "python",
    args: "-m uvicorn main:app --host 0.0.0.0 --port 8000",
    cwd: process.cwd(),
    autorestart: true,
    watch: false
  }]
};
