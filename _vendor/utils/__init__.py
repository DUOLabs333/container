import subprocess
import re
import tempfile
import os
import pathlib
import signal 
import time
import sys
import typing
import shutil
import threading
import contextlib
import warnings
import traceback

for var in ["ROOT","GLOBALS","CLASS","get_all_items"]:
    globals()[var]=None
    
def get_tempdir():
    if os.uname().sysname=="Darwin":
        return "/tmp"
    else:
        return tempfile.gettempdir()
    
class DoesNotExist(Exception):
    pass

def get_value(variable,default):
	if not variable:
		return default
	else:
		return variable

def get_root_directory(root_variable=None,default_value=None):
    root_variable=get_value(root_variable,f"{CLASS.__name__.upper()}_ROOT")
    default_value=get_value(default_value,f"{os.environ['HOME']}/{CLASS.__name__.title()}s")
    return os.path.expanduser(os.getenv(root_variable,default_value))


def list_items_in_root(names,flags):
    global get_all_items
    if not get_all_items:
        get_all_items = lambda root: [_ for _ in sorted(os.listdir(root)) if not _.startswith('.') ] #Fall back to default if no special function is defined
        
    All=get_all_items(ROOT)
    
    for flag in ["started","stopped","enabled","disabled"]:
        if flag in flags:
            names+=[_ for _ in All if flag.title() in CLASS(_).Status() ]
            del flags[flag]

    if "all" in flags:
        names+=All
        del flags["all"]
    if names==[]:
        print(f"No {CLASS.__name__.lower()}s specified!")
        exit()
    return names

def flatten_list(items):
    """Yield items from any nested iterable."""
    for x in items:
        if isinstance(x, typing.Iterable) and not isinstance(x, (str, bytes)):
            for sub_x in flatten_list(x):
                yield sub_x
        else:
            yield x

def print_list(l):
    for element in l:
        if element is None:
            print(end='')
        else:
            print(element)

def split_string_by_char(string,char=':'):
    PATTERN = re.compile(rf'''((?:[^\{char}"']|"[^"]*"|'[^']*')+)''')
    return [_ for _ in list(PATTERN.split(string)) if _ not in ['', char]]


def shell_command(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,arbitrary=False,block=True,env=None):
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr,universal_newlines=True,shell=arbitrary,env=env)
    if block:
        return process.communicate()[0]

def wait_until_pid_exits(pid):
    
    def pid_exists(pid):   
        """ Check For the existence of a unix pid. """
        if shell_command(["ps", "-ostat=",str(pid)])=="Z\n": #Zombie
        
            return False 
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True
            
    while pid_exists(pid):
        time.sleep(0.25)
        
def kill_process_gracefully(pid):
    print(pid)
    try:
        os.kill(pid,signal.SIGTERM)
        try:
            os.waitpid(pid,0)
        except ChildProcessError: #Not a child process so move on
            pass
        wait_until_pid_exits(pid)
    except ProcessLookupError:
        pass
    
def extract_arguments():
    arguments=sys.argv[1:]
    try:
        FUNCTION=arguments[0]
    except IndexError:
        print("No function specified!")
        exit()
    arguments=arguments[1:]
    
    NAMES=[]
    FLAGS=arguments
    for i in range(len(arguments)):
        if not arguments[i].startswith("--"):
            FLAGS=arguments[:i]
            NAMES=arguments[i:]
            break
            
    flags_temp={}
    for flag in FLAGS:
        flag=flag.split('=',1) #Split every flag in FLAGS by '='
        if len(flag)==1:
            flag.append('') #Pad out the flag array
        flag[0]=flag[0][2:] #Remove the '--'
        flags_temp[flag[0]]=flag[1]
        
    FLAGS=flags_temp
    return (NAMES,FLAGS,FUNCTION)

def add_environment_variable_to_string(string,env_var):
    return string+f"; export {env_var}"

def wait(delay=None):
    threading.Event().wait(timeout=delay)

def execute_class_method(class_instance,function):
    if not callable(getattr(class_instance, function.title(),None)):
            print(f"Command {function} doesn't exist!")
            exit()
    else:
        return list(flatten_list([getattr(class_instance,function.title())()]))

def check_if_element_any_is_in_list(elements,_list):
    return any(_ in _list for _ in elements)
    
def export_methods_from_self(self):
    methods={}
    for func in [func for func in dir(self) if callable(getattr(self, func)) and not func.startswith('__')]:
        if not func.startswith('_'):
            methods[func]=getattr(self,func)
    
    return methods

def execute(self,file):
    try:
        if not isinstance(file,str): #Assume file is file object
            code=file.read()
            file.close()
        else:
            code=file
        return exec(code,self.globals,locals())
    except SystemExit as e:
        exit(e)
    except:
        traceback.print_exc()
        self.Stop()
            
def wrap_all_methods_in_class_with_chdir_contextmanager(self,path):
    @contextlib.contextmanager
    def set_directory(path):
        """Sets the cwd within the context
    
        Args:
            path (Path): The path to the cwd
    
        Yields:
            None
        """
    
        origin = os.path.abspath(os.getcwd())
        try:
            if os.path.isdir(path):
                os.chdir(path)
            yield
        finally:
                os.chdir(origin)
    
    def wrapper(func):
        def new_func(*args, **kwargs):
            with set_directory(path):
                return func(*args, **kwargs)
        return new_func
            
    for func in [func for func in dir(self) if callable(getattr(self, func)) and not func.startswith('__')]:
        setattr(self,func,wrapper(getattr(self,func)))
class Class:
    def __init__(self,class_self,_name,_flags,_workdir):
        self.self=class_self
        self.name=self.self.__class__.__name__
        
        self.self.name=_name
        
        self.self.flags=get_value(_flags,{})
        
#        if not os.path.isdir(f"{ROOT}/{self.self.name}"):
#             raise DoesNotExist()
#             return
             
        self.self.temp=os.path.join(get_tempdir(),self.name.title()+"s",self.self.name)
        self.self.log=os.path.join(self.self.temp,"log")
        self.self.lock=os.path.join(self.self.temp,"lock")
        
        os.makedirs(self.self.temp,exist_ok=True)
        
        wrap_all_methods_in_class_with_chdir_contextmanager(self.self,f"{ROOT}/{self.self.name}")
        self.self.workdir=_workdir
        
        self.self.globals=GLOBALS.copy()
        self.self.globals.update(export_methods_from_self(self.self))
        
        
    def stop(self):
        if "Stopped" in self.self.Status():
            return f"{self.name} {self.self.name} is already stopped"
        
        for pid in self.self.Ps("main"):
            kill_process_gracefully(pid)
        
        for file in ["log","lock"]:
            try:
               os.remove(getattr(self.self,file))
            except FileNotFoundError:
                pass

    def restart(self):
        return [self.self.Stop(),self.self.__class__(self.self.name).Start()] #Restart completely new
    
    def get_main_process(self):
        if not os.path.isfile(self.self.lock):
                return []
        else:
            return list(map(int,[_ for _ in shell_command(["lsof","-t","-w",self.self.lock]).splitlines()]))
    
    def list(self):
        return self.self.name
    
        
    def workdir(self,work_dir):
        self.self.workdir=os.path.join(self.self.workdir,work_dir)
        #Remove trailing slashes, but only for strings that are not /
        #if work_dir.endswith('/') and len(work_dir)>1:
            #work_dir=work_dir[:-1]
            #
        #if work_dir.startswith("/"):
            #self.self.workdir=work_dir
        #else:    
            #self.self.workdir+='/'+work_dir
        
        #Remove repeated / in workdir
        #self.self.workdir=re.sub(r'(/)\1+', r'\1',self.self.workdir)

    def status(self):
        if os.path.isfile(self.self.log):
            return ["Started"]
        else:
            return ["Stopped"]

    def loop(self,command,delay=60):
        if isinstance(command,str):
            def func():
                while True:
                    self.self.Run(command)
                    self.self.Wait(delay)
        else:
            def func():
                while True:  
                    command()
                    self.self.Wait(delay)
        self.self.Run("") #Needed to avoid race conditions with a race that's right after --- just run self.self.Run once
        threading.Thread(target=func,daemon=True).start()
       
    def kill_auxiliary_processes(self):
        while self.self.Ps("auxiliary")!=[]: #If new processes were started during an iteration, go over it again, until you killed them all
            for pid in self.self.Ps("auxiliary"):
                kill_process_gracefully(pid)
                
    def log(self):
        shell_command(["less","+G","-f","-r",self.self.log],stdout=None)
    
    def delete(self):
        self.self.Stop()
        shutil.rmtree(f"{ROOT}/{self.self.name}")
    
    def watch(self):
        try:
            shell_command(["tail","-f","--follow=name",self.self.log],stdout=None)
        except KeyboardInterrupt:
            pass
    

