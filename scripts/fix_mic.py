"""Check and fix microphone volume using Windows Core Audio API via ctypes."""
import ctypes
from ctypes import wintypes, POINTER, byref, Structure, c_float

# COM GUIDs
CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

# Constants
E_DATAFLOW_CAPTURE = 1
DEVICE_STATE_ACTIVE = 1

# COM base types
REFGUID = ctypes.c_char_p
CLSCTX_INPROC_SERVER = 1

# ComPtr wrapper
class ComPtr:
    def __init__(self, ptr, interface):
        self.ptr = ptr
        self.interface = interface

class IMMDeviceCollection(Structure):
    pass  # Will be used as opaque pointer

class IAudioEndpointVolume(Structure):
    pass

class IMMDevice(Structure):
    pass

class IMMDeviceEnumerator(Structure):
    pass

# Load ole32
ole32 = ctypes.OleDLL("ole32")
ole32.CoInitializeEx.argtypes = [wintypes.LPCVOID, wintypes.DWORD]
ole32.CoInitializeEx.restype = wintypes.HRESULT

ole32.CoCreateInstance.argtypes = [
    POINTER(GUID), wintypes.LPCVOID, wintypes.DWORD,
    POINTER(GUID), POINTER(wintypes.LPVOID)
]
ole32.CoCreateInstance.restype = wintypes.HRESULT

class GUID(Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]

    def __init__(self, guid_str):
        parts = guid_str.strip("{}").split("-")
        data = [int(p, 16) for p in parts]
        data4 = []
        for b in parts[4]:
            if len(data4) < 8:
                data4.append(int(b, 16))
        d4 = (ctypes.c_ubyte * 8)(*data4)
        super().__init__(data[0], data[1], data[2], d4)

    @classmethod
    def from_string(cls, s):
        parts = s.strip("{}").split("-")
        data1 = int(parts[0], 16)
        data2 = int(parts[1], 16)
        data3 = int(parts[2], 16)
        data4 = (ctypes.c_ubyte * 8)()
        for i in range(8):
            data4[i] = int(parts[3][i*2:(i+1)*2], 16) if i < 4 else int(parts[4][(i-4)*2:(i-3)*2], 16)
        return cls(data1, data2, data3, data4)

    @staticmethod
    def from_string(s):
        parts = s.strip("{}").split("-")
        data1 = int(parts[0], 16)
        data2 = int(parts[1], 16)
        data3 = int(parts[2], 16)
        d4 = (ctypes.c_ubyte * 8)()
        hex_str = "".join(parts[3:])
        for i in range(8):
            d4[i] = int(hex_str[i*2:(i+1)*2], 16)
        return GUID(data1, data2, data3, d4)

    def __repr__(self):
        return f"GUID({self.Data1:#x}, {self.Data2:#x}, {self.Data3:#x}, {bytes(self.Data4).hex()})"

def clsid(s):
    return GUID.from_string(s)

print("Initializing COM...")
ole32.CoInitializeEx(None, 0)

# Create MMDeviceEnumerator
clsid_enumerator = GUID.from_string(CLSID_MMDeviceEnumerator)
iid_enumerator = GUID.from_string(IID_IMMDeviceEnumerator)

ppv = ctypes.c_void_p(0)
hr = ole32.CoCreateInstance(
    byref(clsid_enumerator), None, CLSCTX_INPROC_SERVER,
    byref(iid_enumerator), byref(ppv)
)

if hr != 0:
    print(f"CoCreateInstance failed: {hr:#x}")
    # Fallback: check via sounddevice
    import sounddevice as sd
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] > 0:
            print(f"Input [{i}]: {dev['name']}")
else:
    print(f"MMDeviceEnumerator created: {ppv}")

    # Define vtable structure
    # For simplicity, let's use a simpler approach

ole32.CoUninitialize()
