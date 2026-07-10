"""Print Wacom STU SDK and USB device diagnostics."""

from __future__ import annotations

from services.wacom.stu_sdk import WacomSTUSDK, WacomSTUSDKError


def main() -> int:
    try:
        sdk = WacomSTUSDK()
        devices = sdk.get_usb_devices()
    except WacomSTUSDKError as error:
        print(f"ERROR: {error}")
        return 1

    print(f"DLL: {sdk.dll_path}")
    print(f"USB devices: {len(devices)}")
    for index, device in enumerate(devices, start=1):
        print(
            f"{index}. {device.model_name} "
            f"vendor=0x{device.vendor_id:04x} "
            f"product=0x{device.product_id:04x} "
            f"version=0x{device.device_version:04x}"
        )
        print(f"   file: {device.file_name}")
        if device.bulk_file_name:
            print(f"   bulk: {device.bulk_file_name}")
        if device.is_wacom_stu:
            try:
                info = sdk.get_tablet_info(device)
            except WacomSTUSDKError as error:
                print(f"   connect: ERROR: {error}")
            else:
                print(
                    f"   connect: OK model={info.model_name} "
                    f"firmware={info.firmware_major}.{info.firmware_minor} "
                    f"screen={info.screen_width}x{info.screen_height} "
                    f"tablet={info.tablet_max_x}x{info.tablet_max_y} "
                    f"pressure={info.tablet_max_pressure}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
