import os, glob
print("cwd:", os.getcwd())
print("argv0 dir:", os.path.dirname(os.path.abspath(__file__)))
print("list cwd:", sorted(os.listdir(".")))
print("list argvdir:", sorted(os.listdir(os.path.dirname(os.path.abspath(__file__)))))
for root in ["/", "/home", "/home/alan", "/home/alan/projects",
             "/home/alan/projects/multi-agent-system"]:
    try:
        print("LS", root, ":", sorted(os.listdir(root))[:30])
    except Exception as e:
        print("LS", root, "ERR", e)
print("bookings.db:", glob.glob("/home/alan/projects/multi-agent-system/**/bookings.db", recursive=True)[:20])
print("main.py:", glob.glob("/home/alan/projects/multi-agent-system/**/main.py", recursive=True)[:20])
