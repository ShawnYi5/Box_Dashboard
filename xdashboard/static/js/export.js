function setCheckboxValueInit()
{
	$('input[type="checkbox"]').each(function(){
		if($(this).attr('checked'))
		{
			$(this).attr('isChecked','1');
		}
		else
		{
			$(this).attr('isChecked','0');
		}
	});

	$('input[type="checkbox"]').each(function(){
		$(this).click(function(){
			if($(this).attr('checked'))
			{
				$(this).attr('isChecked','1');
			}
			else
			{
				$(this).attr('isChecked','0');
			}
		});
	});
}

function setCheckboxValue()
{
	$('input[type="checkbox"]').each(function(){
		if($(this).attr('isChecked') == '1')
		{
			$(this).attr('checked',true);
		}
		else
		{
			$(this).attr('checked','');
		}
	});
}

function Export(obj)
{
	var self = this;
	
	this.configObj = obj;
	
	this.continueNum = 1;
	this.retryNum = 1;
	this.param = null;
	this.pageStart = null;
	this.pageEnd = null;
	this.pwd = this.configObj.pwd;
	
	this.pageDivId = 'exportPageDiv';
	this.pageDiv = '<div id="' + this.pageDivId + '" style="display:none" class="exportFormRemove"><table width="100%" border="0" cellspacing="0" cellpadding="0"><tr><td height="30" colspan="2">共 <span id="exportPageRecords"></span> 条记录，每页 <span id="exportPageLimit"></span> 条，共 <span id="exportPageNum"></span> 页</td></tr><tr><td width="85%" height="30">请输入您要导出的页数范围：<input name="exportPageStart" id="exportPageStart" type="text" size="10" /> - <input name="exportPageEnd" id="exportPageEnd" type="text" size="10"/></td><td width="15%"></td></tr><tr><td height="30">导出文件的目标位置：<input name="exportFileLocation" id="exportFileLocation" type="text" size="31" /></td><td width="15%"><a href="#" id="exportPageSumbit">导出</a></td></tr><tr><td colspan="2" height="30" class="form_tips">注：必须填写安装服务器的那台计算机存在的绝对路径，路径不能为根目录，不能含中文。为空则为默认路径</td></tr><tr><td colspan="2"><div id="exportPageMsg" style="color:#FF0000"></div></td></tr><tr><td colspan="2"><div id="exportPageResult" style="height:170px;overflow:auto"></div></td></tr></table></div>';
	this.filePageNum = 50;
	
	this.init = function()
	{
		if($('#'+this.pageDivId).length < 1)
		{
			$('body').append(this.pageDiv);
		}
		
		$('#exportPageStart').val('');
		$('#exportPageEnd').val('');
		$('#exportFileLocation').val('');
		$('#' + this.configObj.buttonId).unbind();
		$('#exportPageSumbit').unbind();
		
		$('#' + this.configObj.buttonId)
		.button()
		.click(function(){self.openPageDialog();});
		
		$('#exportPageSumbit')
		.button()
		.click(function(){self.pageSumit();});
	};
	
	this.openPageDialog = function()
	{
		//当查询框里有checkbox时,这里再进行初始化就会有问题。jqGrid加载完了再进行一次
		//setCheckboxValueInit();
		
		var list = jQuery('#' + this.configObj.listId);
		var records = list.getGridParam('records');
		var limit = list.getGridParam('rowNum');
		var num = Math.ceil(records/limit);
		
		$('#exportPageRecords').html(records);
		$('#exportPageLimit').html(limit);
		$('#exportPageNum').html(num);
		
		$('#exportPageMsg').html('');
		$('#exportPageResult').html('');
			
		$('#' + this.pageDivId).dialog({
			modal: true,
			title: '导出指定页',
			width: 500,
			height: 470,
			buttons: {
				'关闭': function(){
					$(this).dialog('close');
				}
			},
			close: function(){}
		});	
	};
	
	this.pageSumit = function()
	{
		this.pageStart = parseInt($('#exportPageStart').val());
		this.pageEnd = parseInt($('#exportPageEnd').val());
		this.limit = parseInt($('#exportPageLimit').html());
		
		//验证输入
		if(this.pageStart != $('#exportPageStart').val() || this.pageEnd != $('#exportPageEnd').val())
		{
			openWarningDialog('警告','开始页数或结束页数必须为整数数字');
			return;
		}
		if(this.pageStart < 1 || this.pageEnd > parseInt($('#exportPageNum').html()) || this.pageEnd < this.pageStart)
		{
			openWarningDialog('警告','开始页数或结束页数取值范围为1-'+$('#exportPageNum').html()+',并且结束页数不能小于开始页数');
			return;
		}
		
		//已经在导出中了，点击按纽无效
		if(this.continueNum > 1) return;
		if( this.pwd )
		{
			$('#exportPageMsg').html('<img src="images/loading.gif" height="30" width="30" /> 导出中，请稍侯... <br>文件打开密码为：'+this.pwd);
		}
		else
		{
			$('#exportPageMsg').html('<img src="images/loading.gif" height="30" width="30" /> 导出中，请稍侯...');
		}
		$('#exportPageResult').html('')
		
		setCheckboxValue();
		this.param = $('#' + this.configObj.searchId).formSerialize();
		if(this.configObj.searchId2)
		{
			this.param += '&'+$('#' + this.configObj.searchId2).formSerialize();
		}
		this.param += '&fileLocation='+encodeURI($('#exportFileLocation').val());
	
		this.continueNum = 1;
		this.exportPage();
	};	
	
	this.exportPage = function exportPage()
	{
		var This = this;
		//每filePageNum页导出一个文件,根据循环次数计算开始和结束页数
		if(this.continueNum == 1)
		{
			var currentStart = this.pageStart;
		}
		else
		{
			var currentStart = this.pageStart + (this.continueNum-1) * this.filePageNum;
		}
		
		if(this.pageEnd < (this.pageStart + this.continueNum * this.filePageNum))
		{
			var currentEnd = this.pageEnd;
		}
		else
		{
			var currentEnd = this.pageStart + (this.continueNum * this.filePageNum) - 1;
		}
		$.ajax({
			url: this.configObj.exportUrl + "&pageStart=" + currentStart + "&pageEnd=" + currentEnd + "&pageLimit=" + this.limit+"&pwd="+this.pwd, 	
			type: 'POST',
			data: this.param,
			error: function(x, e){
				if(x.status == 500)
				{
					if(self.retryNum > 3)
					{
						self.retryNum = 0;
						
						var str = currentStart + '页-' + currentEnd + '页导出失败<br>';
						$('#exportPageResult').append(str);
						
						//判断该页是否为最后一页
						if(self.pageEnd < (self.pageStart + (self.continueNum + 1) * 150))
						{
							var nextEnd = self.pageEnd;
						}
						else
						{
							var nextEnd = self.pageStart + ((self.continueNum + self.filePageNum) * 150) - 1;
						}
						if(nextEnd == self.pageEnd)
						{
							self.continueNum = 1;
							if( This.pwd )
							{
								$('#exportPageMsg').html('导出完成，请点击文件名下载。<br>文件打开密码为：'+This.pwd);
							}
							else
							{
								$('#exportPageMsg').html('导出完成，请点击文件名下载。');
							}
						}
						else
						{
							self.continueNum++;
							self.exportPage();
						}
					}
					else
					{
						self.retryNum++;
						setTimeout(function(){self.exportPage();}, 3000);
					}
				}
			},	
			success: function(data)
			{
				jsonObj = JSON.parse(data);
				if(jsonObj.r == 0)
				{				
					self.continueNum++;
					
					if(jsonObj.defaultFileLocation == 1)
					{
						var str = '<a href="/printfiles/tmpExcel/'+jsonObj.filename+'" target="_blank">'+jsonObj.filename+'</a><br>';	
					}
					else
					{
						var str = jsonObj.filename+'<br>';	
					}
					
					$('#exportPageResult').append(str);
					
					if(currentEnd == self.pageEnd)
					{
						if(jsonObj.defaultFileLocation == 1)
						{
							if( This.pwd )
							{
								$('#exportPageMsg').html('导出完成，请点击文件名下载。<br>文件打开密码为：'+This.pwd);
							}
							else
							{
								$('#exportPageMsg').html('导出完成，请点击文件名下载。');	
							}
						}
						else
						{
							if( This.pwd )
							{
								$('#exportPageMsg').html('导出完成，请直接到服务器"'+$('#exportFileLocation').val()+'"查看导出文件。<br>文件打开密码为：'+This.pwd);
							}
							else
							{
								$('#exportPageMsg').html('导出完成，请直接到服务器"'+$('#exportFileLocation').val()+'"查看导出文件。');
							}
							
						}
						self.continueNum = 1;
					}
					else
					{
						self.exportPage();
					}
				}
				else
				{
					$('#exportPageMsg').html('导出失败，' + jsonObj.e);
					self.continueNum = 1;
				}
			}
		});
	}
}
