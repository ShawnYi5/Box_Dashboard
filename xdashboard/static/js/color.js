// JavaScript Document中文
var ColorHex=new Array('00','33','66','99','CC','FF')
var SpColorHex=new Array('FF0000','00FF00','0000FF','FFFF00','00FFFF','FF00FF')
var current=null
function getEvent() ////event ie and firefox 的兼容问题
{
	if(document.all)
	return window.event;//如果是ie
	func=getEvent.caller;
	while(func!=null)
	{
		var arg0=func.arguments[0];
		if(arg0)
		{
			if((arg0.constructor==Event || arg0.constructor ==MouseEvent) || (typeof(arg0)=="object" && arg0.preventDefault && arg0.stopPropagation))
			{
				return arg0;
			}            
		}
		func=func.caller;
	}
	return null;
}
function getElement()   //IE下的srcElement 与firefox 的兼容
{
	var evt=getEvent();
	var element=evt.srcElement || evt.target;
	return element;
}

function InitColorBox(panelId)
{
var colorpanel=document.getElementById(panelId);
var colorTable=''
for (i=0;i<2;i++)
{
	for (j=0;j<6;j++)
	{
		colorTable=colorTable+'<tr height=12>'
		colorTable=colorTable+'<td width=11 style="background-color:#000000">'
		
		if (i==0)
		{
			colorTable=colorTable+'<td width=11 style="background-color:#'+ColorHex[j]+ColorHex[j]+ColorHex[j]+'">'
		} 
		else
		{
			colorTable=colorTable+'<td width=11 style="background-color:#'+SpColorHex[j]+'">'}    
			colorTable=colorTable+'<td width=11 style="background-color:#000000">'
			for (k=0;k<3;k++)
			{
				for (l=0;l<6;l++)
				{
				colorTable=colorTable+'<td width=11 style="background-color:#'+ColorHex[k+i*3]+ColorHex[l]+ColorHex[j]+'">'
				}
			}
	}
}

colorTable='<table width=253 border="0" cellspacing="0" cellpadding="0" ' 
			+ 'style="border:1px #000000 solid;border-bottom:none;border-collapse: collapse" bordercolor="000000">'
			+ '<tr height=30><td colspan=21 bgcolor=#cccccc>'
			+ '<table cellpadding="0" cellspacing="1" border="0" style="border-collapse: collapse">'
			+ '<tr><td width="3"><td><input type="text" name="DisColor" id="DisColor" size="6" disabled ' 
			+ 'style="border:1px solid #000000;background-color:#ffff00"></td>'
			+ '<td width="3"><td></td></tr></table></td></table>'
			+ '<table border="1" cellspacing="0" cellpadding="0" style="border-collapse: collapse" bordercolor="000000" ' 
			+ 'onmouseover="doOver()" onmouseout="doOut()" onclick="doclick()" style="cursor:pointer;">'
			+ colorTable + '</table>';   
colorpanel.innerHTML=colorTable;
}

function doOver() 
{
	obj=getElement();
	if ((obj.tagName=="TD") && (current!=obj)) 
	{
		if (current!=null)
		{
			current.style.backgroundColor = current._background;
		}
		obj._background = obj.style.backgroundColor;
		var DisColor=document.getElementById("DisColor");
		/* var HexColor=document.getElementById("HexColor");
		HexColor.value = obj.style.backgroundColor.toUpperCase();*/
		DisColor.style.backgroundColor = obj.style.backgroundColor;
		obj.style.backgroundColor = "white"
		current =obj
	}
}//将颜色值字母大写

function SetInitColor(color)
{
	var DisColor=document.getElementById("DisColor");
	DisColor.style.backgroundColor = color;
}

function doOut() 
{
	if (current!=null) 
	{
		current.style.backgroundColor = current._background.toUpperCase();
	}
}

function doclick()
{
	obj=getElement();    
	if (obj.tagName == "TD")
    {
        var clr = obj._background;
        clr = clr.toUpperCase(); //将颜色值大写
        if (targetElement)
        {
            //给目标无件设置颜色值
            targetElement.style.backgroundColor = "#"+color10To16(clr);
        }
        DisplayClrDlg(false);
        return clr;
    }
}