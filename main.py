"""部署進入點。

PaaS（Zeabur、Railway、Render 等）會自動偵測 ``main.py``。

這裡刻意寫死對外綁定並以 8080 為預設埠：容器內若綁 ``127.0.0.1``，平台的
反向代理**完全連不到**，而多數平台的預設服務埠就是 8080。平台若有注入
``PORT``／``HOST`` 則以其為準。本機開發請改用 ``python -m fcn.cli serve``，
那條路徑仍預設只綁本機。

公開部署請務必設定 ``FCN_TOKEN`` 環境變數啟用通行碼保護。
"""

import os

from fcn.webapp import serve

if __name__ == "__main__":
    serve(
        host=os.environ.get("HOST") or "0.0.0.0",
        port=int(os.environ.get("PORT") or 8080),
    )
