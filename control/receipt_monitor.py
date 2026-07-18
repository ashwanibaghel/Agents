import os
import json
import time

class ReceiptMonitor:
    def __init__(self, receipt_dir="state/receipts", poll_interval=1.0, timeout=10.0):
        self.receipt_dir = os.path.abspath(receipt_dir)
        self.poll_interval = poll_interval
        self.timeout = timeout
        os.makedirs(self.receipt_dir, exist_ok=True)

    def check_receipt(self, task_id: str, conversation_id: str = None) -> dict:
        """Locate, parse, and defensively validate a completion receipt file."""
        receipt_path = os.path.join(self.receipt_dir, f"{task_id}.json")
        
        # Path traversal check
        if not os.path.abspath(receipt_path).startswith(self.receipt_dir):
            return {"success": False, "error": "Path traversal detected."}
            
        if not os.path.exists(receipt_path):
            return None
            
        try:
            content = None
            for enc in ["utf-8-sig", "utf-16", "cp1252"]:
                try:
                    with open(receipt_path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                raise ValueError("Could not decode file with utf-8, utf-16, or cp1252.")
            data = json.loads(content)
        except json.JSONDecodeError as jde:
            return {"success": False, "error": f"Malformed JSON: {str(jde)}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to read receipt: {str(e)}"}
            
        # Validate schema
        required = ["task_id", "status", "summary", "completed_at"]
        for field in required:
            if field not in data:
                return {"success": False, "error": f"Missing required field '{field}' in receipt."}
                
        # Validate task_id
        if data["task_id"] != task_id:
            return {"success": False, "error": f"Task ID mismatch: receipt has '{data['task_id']}', expected '{task_id}'."}
            
        # Validate conversation_id if present in receipt
        receipt_conv_id = data.get("conversation_id")
        if receipt_conv_id and conversation_id and receipt_conv_id != conversation_id:
            return {"success": False, "error": f"Conversation ID mismatch: receipt has '{receipt_conv_id}', expected '{conversation_id}'."}
            
        # Validate status
        allowed_statuses = ["DONE", "BLOCKED", "FAILED"]
        status = data["status"].upper()
        if status not in allowed_statuses:
            return {"success": False, "error": f"Invalid status '{data['status']}' in receipt."}
            
        return {
            "success": True,
            "status": status,
            "receipt_data": data,
            "path": receipt_path
        }

    def wait_for_receipt(self, task_id: str, conversation_id: str = None, heartbeat_callback = None, heartbeat_interval = 15.0) -> dict:
        """Poll the receipt directory up to timeout limit, sending heartbeats periodically."""
        start_time = time.time()
        last_heartbeat = 0.0
        while time.time() - start_time < self.timeout:
            res = self.check_receipt(task_id, conversation_id)
            if res:
                return res
            
            # Send heartbeat periodically
            now = time.time()
            if heartbeat_callback and (now - last_heartbeat) >= heartbeat_interval:
                try:
                    heartbeat_callback(task_id)
                except Exception:
                    pass
                last_heartbeat = now
                
            time.sleep(self.poll_interval)
            
        return {
            "success": False,
            "error": f"Receipt monitoring timed out after {self.timeout} seconds.",
            "timeout": True
        }
