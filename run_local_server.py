from app import app, socketio


if __name__ == "__main__":
    socketio.run(
        app,
        debug=False,
        host="127.0.0.1",
        port=5000,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
