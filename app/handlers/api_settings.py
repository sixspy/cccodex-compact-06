from app.handlers.base import BaseHandler
from app.services import settings as settings_svc


class ApiSettings(BaseHandler):
    def get(self):
        self.require_user()
        self.json(settings_svc.settings_snapshot())

    def post(self):
        operator = self.require_user()
        body = self.read_json()
        result = settings_svc.update_setting(
            str(body.get("key") or ""),
            str(body.get("value") or ""),
            operator=operator,
            note=str(body.get("note") or ""),
        )
        self.json(result)


class ApiThresholdCutovers(BaseHandler):
    def get(self):
        self.require_user()
        self.json(settings_svc.list_cutovers())
