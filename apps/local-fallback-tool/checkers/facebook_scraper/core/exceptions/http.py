class BadRequestException(Exception):
    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(detail)


class ExternalAPIException(Exception):
    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(detail)
