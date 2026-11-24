class ReauthenticationRequiredError(Exception):
    """外部APIを呼び出す際に再認証が必要になったなときにスローされる例外。"""
    pass


class ConversationNotFoundError(LookupError):
    """差分取得の起点に指定した会話履歴のドキュメントが見つからないときにスローされる例外。

    `ConversationRepository.list_for_client(after_id=... / before_id=...)` で、
    基準のドキュメントが存在しない（TTL で消えた・削除された・別 DB の id など）
    場合に、`since` の有無によらず送出する。呼び出し側は
    「起点が失われたので全件を取り直す」合図として扱う（HTTP なら 404 / 410 など）。
    """
    pass


class LLMError(Exception):
    """LLM 呼び出しに失敗したときにスローされる例外。

    Ollama / OpenAI 互換プロバイダへのリクエスト送信やレスポンス解析で
    発生した例外をラップし、呼び出し側が LLM 由来の失敗を型で識別できるようにする。

    送信の失敗をラップしたものは、失敗したプロバイダー名と失敗の種類を属性に持つ
    （resolver による別プロバイダーへの再選択と、失敗したプロバイダーの一時記憶に使う）。
    メッセージだけで作ったもの（送信前検査の例外など）では既定値のまま。

    Attributes:
        provider_name: 送信先の具体プロバイダー名。送信の失敗でなければ `None`。
        error_kind: 失敗の種類。`"http"`（HTTP エラー）/ `"connection"`（接続エラー）/
            `"timeout"`（タイムアウト）/ `"invalid_response"`（応答の解釈の失敗）/
            `"other"`（それ以外・送信の失敗でないもの）。
        status: HTTP エラーならそのステータス。それ以外は `None`。
        retry_after: HTTP エラーの応答の `Retry-After`（秒）。無い・読めなければ `None`。
    """

    def __init__(
        self,
        *args: object,
        provider_name: str | None = None,
        error_kind: str = "other",
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """メッセージと、送信の失敗なら失敗の情報を受け取る。"""
        super().__init__(*args)
        self.provider_name = provider_name
        self.error_kind = error_kind
        self.status = status
        self.retry_after = retry_after


class LlmSendBlockedError(LLMError):
    """LLM へ送る直前の検査で送信を止めたときにスローされる例外。

    拒否リストの語がリクエストボディに含まれていた場合に送出する。
    メッセージは固定文言で、一致した語やリクエスト本文を含めない。
    """
    pass


class LlmSendGuardUnavailableError(LLMError):
    """拒否リストが指定されているのに使えないときにスローされる例外。

    リストファイルが読めない・JSON でない・文字列配列でない・空文字を含む場合に送出する。
    `bot.py` の起動時に出れば起動を止め、起動時の読み込みを経ない呼び出しでは送信しない。
    メッセージにリストの中身は含めない。
    """
    pass
