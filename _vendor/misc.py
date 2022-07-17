def load_dependencies(self,layer):
    with open(f"{utils.ROOT}/{layer}/container-compose.py") as fh:        
       root = ast.parse(fh.read())
       for node in ast.iter_child_nodes(root):
           if isinstance(node, ast.Expr) and isinstance(node.value,ast.Call):
               function=node.value.func.id
               if function in ["Layer","Base","Env","Shell"]:
                   arguments=[eval(ast.unparse(val)) for val in node.value.args]
                   getattr(self,function)(*arguments) #Run function

def chroot_command(self,command):
    if self.namespaces.user:
        result = ["unshare",f"--map-user={self.uid}",f"--map-group={self.gid}","--root=merged"] #Unshare is available so use it  
    else:
        result = ["chroot",f"--userspec={self.uid}:{self.gid}", "merged"] # Unshare does not exist, so use chroot
        
    result+=[f"{self.shell}","-c",f"{self.env}; cd {self.workdir}; {command}"]
    
    
    if self.namespaces.net:
        result=["sudo","ip","netns","exec",self.netns,"sudo","-u",getpass.getuser()]+result
    
    if sys.platform!="cygwin" and not self.namespaces.user:
        result=["sudo"]+result
        
    return result
     
