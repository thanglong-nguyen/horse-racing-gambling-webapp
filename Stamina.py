from enum import Enum

class StaminaState(Enum):
    FULL_SPEED = 1    
    HIGH = 2          
    LOW = 3           
    EXHAUSTED = 4 

def get_stamina_state(stamina):
    if stamina > 75:
        return StaminaState.FULL_SPEED
    elif stamina > 50:
        return StaminaState.HIGH
    elif stamina > 25:
        return StaminaState.LOW
    else:
        return StaminaState.EXHAUSTED