import os
import subprocess
import sys
import time

is_windows = sys.platform.startswith("win")
is_linux = sys.platform.startswith("linux")
is_mac = sys.platform.startswith("darwin")

venv_exists = os.path.exists("venv")

if os.name == 'nt':
    pip_install_command = 'python -m pip install --upgrade pip'
elif os.name == 'posix':
    pip_install_command = 'python3 -m pip install --upgrade pip -q > /dev/null'
else:
    raise Exception('Unsupported OS')

def activate_and_run_script():
    global venv_exists

    if not venv_exists:
        print("Creating and activating virtual environment...")
        try:
            subprocess.run([sys.executable, '-m', 'venv', 'venv'], check=True)
            print("Virtual environment created successfully")
            venv_exists = True
        except subprocess.CalledProcessError as e:
            print(f"Error creating virtual environment: {e}")
            raise

    if is_windows:
        activate_command = "venv\\Scripts\\activate.bat"
        python_command = "venv\\Scripts\\python.exe"
    else:
        activate_command = "source venv/bin/activate"
        python_command = "venv/bin/python3"

    if not venv_exists or not os.environ.get("VIRTUAL_ENV"):
        if is_windows:
            try:
                # Активация виртуального окружения
                subprocess.run(
                    f"{activate_command}",
                    shell=True,
                    check=True
                )
                
                # Обновление pip с подавлением вывода
                subprocess.run(
                    [python_command, "-m", "pip", "install", "--upgrade", "pip"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
                
                # Запуск основного скрипта
                subprocess.run([python_command, "core/main.py"], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error during Windows activation/execution: {e}")
                raise
        else:
            try:
                activate_cmd = f"source venv/bin/activate && {python_command} -m pip install --upgrade pip -q > /dev/null && {python_command} core/main.py"
                subprocess.run(['bash', '-c', activate_cmd], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error during Unix activation/execution: {e}")
                raise
    else:
        print("Virtual environment already activated, running main script...")
        try:
            subprocess.run([python_command, "core/main.py"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error running main script: {e}")
            raise

def check_dependencies():
    try:
        subprocess.run([sys.executable, '-m', 'venv', '--help'], 
                      stdout=subprocess.PIPE, 
                      stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        print("ERROR: venv module is not available. Please install python3-venv package.")
        return False

try:
    if not check_dependencies():
        sys.exit(1)

    if not venv_exists:
        activate_and_run_script()
    else:
        if os.environ.get("VIRTUAL_ENV"):
            print("Virtual environment is already activated")
            subprocess.run([python_command, "core/main.py"], check=True)
        else:
            activate_and_run_script()

except KeyboardInterrupt:
    time.sleep(1)
    sys.exit()
except Exception as e:
    print(f"Unexpected error occurred: {str(e)}")
    raise