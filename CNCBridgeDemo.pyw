"""Double-click to open the CNCBridge demo. Also the entry script for build_exe.bat."""
try:
    from demo.control import main
except ImportError as error:
    import tkinter
    from tkinter import messagebox

    tkinter.Tk().withdraw()  # no empty window behind the message
    messagebox.showerror(
        "CNCBridge demo",
        "The demo files are missing from this copy of CNCBridge, so the demo cannot start.\n\n"
        "Use a copy that has the demo folder.\n\n" + str(error))
else:
    main()
