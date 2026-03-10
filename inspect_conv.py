import homeassistant.components.conversation as conversation
import inspect

print(f"Attributes in conversation: {dir(conversation)}")

for name, obj in inspect.getmembers(conversation):
    if inspect.isclass(obj) and "Agent" in name:
        print(f"Found class: {name}")

try:
    from homeassistant.components.conversation import models
    print(f"Attributes in conversation.models: {dir(models)}")
except ImportError:
    print("conversation.models not found")
