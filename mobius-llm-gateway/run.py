from app.main import app

if __name__ == "__main__":
    import uvicorn
    # Use uvloop and httptools for maximum performance
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8080,
        loop="uvloop",
        http="httptools",
        workers=4,  # Adjust based on CPU cores
        log_level="info"
    )
