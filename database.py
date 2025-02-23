# database.py
import json
import os
import time
from typing import Dict, Union

class Database:
    def __init__(self, file_path: str = "data.json"):
        self.file_path = file_path
        self.data = self._load_data()

    def _load_data(self) -> Dict:
        if not os.path.exists(self.file_path):
            return {"users": {}}
        with open(self.file_path, "r") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=2)

    def save_thumbnail(self, user_id: int, file_id: str):
        self.data["users"].setdefault(str(user_id), {})["thumbnail"] = file_id
        self._save()

    def get_thumbnail(self, user_id: int) -> Union[str, None]:
        return self.data["users"].get(str(user_id), {}).get("thumbnail")

    def delete_thumbnail(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("thumbnail", None)
            self._save()

    def add_active_task(self, user_id: int, message_id: int):
        self.data["users"].setdefault(str(user_id), {})["active_task"] = message_id
        self._save()

    def get_active_task(self, user_id: int) -> Union[int, None]:
        return self.data["users"].get(str(user_id), {}).get("active_task")

    def delete_active_task(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("active_task", None)
            self._save()

    def set_waiting_for_name(self, user_id: int, status: bool):
        self.data["users"].setdefault(str(user_id), {})["waiting_for_name"] = status
        self._save()

    def is_waiting_for_name(self, user_id: int) -> bool:
        return self.data["users"].get(str(user_id), {}).get("waiting_for_name", False)

    def set_original_message(self, user_id: int, message_id: int):
        self.data["users"].setdefault(str(user_id), {})["original_msg_id"] = message_id
        self._save()

    def get_original_message(self, user_id: int) -> Union[int, None]:
        return self.data["users"].get(str(user_id), {}).get("original_msg_id")

    def save_new_name(self, user_id: int, new_name: str):
        self.data["users"].setdefault(str(user_id), {})["new_name"] = new_name
        self._save()

    def get_new_name(self, user_id: int) -> Union[str, None]:
        return self.data["users"].get(str(user_id), {}).get("new_name")

    def delete_new_name(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("new_name", None)
            self._save()

    def set_last_action_time(self, user_id: int, timestamp: float):
        self.data["users"].setdefault(str(user_id), {})["last_action_time"] = timestamp
        self._save()

    def get_last_action_time(self, user_id: int) -> Union[float, None]:
        return self.data["users"].get(str(user_id), {}).get("last_action_time")

    def set_premium_until(self, user_id: int, timestamp: float):
        self.data["users"].setdefault(str(user_id), {})["premium_until"] = timestamp
        self._save()

    def get_premium_until(self, user_id: int) -> Union[float, None]:
        return self.data["users"].get(str(user_id), {}).get("premium_until")

    def remove_premium(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("premium_until", None)
            self._save()

    def add_action_count(self, user_id: int):
        user = self.data["users"].setdefault(str(user_id), {})
        user["actions_count"] = user.get("actions_count", 0) + 1
        self._save()

    def get_action_count(self, user_id: int) -> int:
        return self.data["users"].get(str(user_id), {}).get("actions_count", 0)

    def get_all_users(self):
        return self.data.get("users", {})

db = Database()
