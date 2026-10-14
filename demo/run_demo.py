"""Start the integrated CNCBridge simulation.

The control window starts an isolated CNCBridge workspace, a simulated SMT
machine, and a local customer MES receiver.  The simulator writes realistic
multi-table CSV logs in chunks so the normal CNCBridge stability, parsing,
delivery, duplicate and retry paths are exercised without changing the main
application UI.

Run from the project root with::

    python -m demo.run_demo

``CNCBridgeDemo.pyw`` remains the double-clickable Windows entry point.
"""

from .control import main


if __name__ == "__main__":
    main()
