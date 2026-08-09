"""部署進入點。

PaaS（Zeabur、Railway、Render 等）會自動偵測 ``main.py``，並以 ``PORT``
環境變數指派通訊埠。實際的位址與埠號解析在 :func:`fcn.webapp.resolve_bind`。

公開部署請務必設定 ``FCN_TOKEN`` 環境變數啟用通行碼保護。
"""

from fcn.webapp import serve

if __name__ == "__main__":
    serve()
