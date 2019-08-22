function getaciTreeChecked(treeid){
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	var ids='';
	api.radios(children, true).each(api.proxy(function(element) {
		var item = $(element);
		var id=String(this.getId(item));
		if(id.substring(0,3)!='ui_')
		{
			if(ids=='')
			{
				ids = id;
			}
			else
			{
				ids += ','+id;
			}
		}
	}, true));

	return ids;

}

function getaciTreeNameChecked(treeid){
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	var ids='';
	api.radios(children, true).each(api.proxy(function(element) {
		var item = $(element);
		if(ids=='')
		{
			ids = this.getLabel(item);
		}
		else
		{
			ids += ','+this.getLabel(item);
		}
	}, true));

	return ids;

}

function GetAciTreeParent(treeid,id)
{
	var api = $('#'+treeid).aciTree('api');
	var parentid='none';
	var children=api.children(null, true, true);
	var ids='';
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);
		if(id == this.getId(item))
		{
			parentid=this.getId(this.parent(item));
			return false;
		}

	}, true));

	return parentid;
}

function CheckAciTree(treeid,id)
{
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	var ids='';
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);

		if(id == this.getId(item))
		{
			//this._radioDOM.check(item, true);
			this.check(item);
		}
		else
		{
			this.uncheck(item);
		}

	}, true));
}

function CheckAciTreeRadioByLabel(treeid, label)
{
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	var ids='';
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);
		if(label === this.getLabel(item)){
			this.check(item);
			return false;
		}
	}, true));
}

function initAciTree(treeid,url)
{
	return $('#'+treeid).aciTree({
			autoInit: true,
			ajax: {
				url: url
			},
			checkbox: true,
			radio: true,
			selectable: true
		}).aciTree('api');
}

function RefreshAciTree(treeid,url,callback,p)
{
	$('#'+treeid).aciTree().aciTree('api').destroy({
		success: function() {
			initAciTree(treeid,url);
			if(typeof(callback)=='function')
			{
				callback(p);
			}
		}
	});
}

// 遍历AciTree，获取所有选中、未选中checkbox的id号
// checked=true选中的，checked=false未选中的
function getAciTreeBoxChecked(treeid, checked){
	if(checked == undefined){
		checked=true;
	}
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	var ids='';
	api.checkboxes(children, checked).each(api.proxy(function(element) {
		var item = $(element);
		var id=String(this.getId(item));
		if(id.substring(0,3)!='ui_')
		{
			if(ids=='')
			{
				ids = id;
			}
			else
			{
				ids += ','+id;
			}
		}
	}, true));

	return ids;
}

// 遍历AciTree, 获取未选中的Node信息
// 遍历的元素：checkboxes
function getUnCheckedBox(treeid) {
    var api = $('#'+treeid).aciTree('api');
    var children = api.children(null, true, true);
    var nodes = [];
	api.checkboxes(children, false).each(api.proxy(function(element) {
		var item = $(element);
        nodes.push({'id': this.getId(item), 'lable': this.getLabel(item), 'level': this.level(item)});
	}, true));

    return nodes;
}

// 遍历AciTree, 获取选中的Node信息
// 遍历的元素：checkboxes
function getCheckedBox(treeid) {
    var api = $(treeid).aciTree('api');
    var children = api.children(null, true, true);
    var nodes = [];
	api.checkboxes(children, true).each(api.proxy(function(element) {
		var item = $(element);
        nodes.push({'id': this.getId(item), 'lable': this.getLabel(item), 'level': this.level(item)});
	}, true));

    return nodes;
}

// 遍历AciTree, 获取选中的Node信息
// 遍历的元素：checkboxes
// 附加父node的信息
function getCheckedBoxInfo(treeid) {
    var api = $(treeid).aciTree('api');
    var children = api.children(null, true, true);
    var nodes = [];
	api.checkboxes(children, true).each(api.proxy(function(element) {
		var item = $(element);
		var parent = this.parent(item);
        nodes.push({
			'id': String(this.getId(parent) + '|' + this.getId(item)),
			'lable': String(this.getLabel(parent) + ': ' + this.getLabel(item))
        });
	}, true));

    return nodes;
}

// 遍历checkboxes, 获取未选中的Node信息: 获取排除的卷
function getUnCheckedVolsInfo(treeid) {
    var api = $(treeid).aciTree('api');
    var children = api.children(null, true, true);
    var nodes = [];
	api.checkboxes(children, false).each(api.proxy(function(element) {
		var item = $(element);
		if(this.level(item) === 2){
			var parent = this.parent(item);
			var host = this.parent(parent);
			nodes.push({
				'id': this.getId(item),
				'host_id': this.getId(host),
				'lable': this.getLabel(host) + ': ' + this.getLabel(item)
       	 	});
		}
	}, true));

    return nodes;
}

// 遍历checkboxes, 获取未选中的Node信息: 获取排除的磁盘
function getUnCheckedDisksInfo(treeid) {
    var api = $(treeid).aciTree('api');
    var children = api.children(null, true, true);
    var nodes = [];
	api.checkboxes(children, false).each(api.proxy(function(element) {
		var item = $(element);
		if(this.level(item) === 1){
			var host = this.parent(item);
			nodes.push({
				'id': this.getId(item),
				'host_id': this.getId(host),
				'lable': this.getLabel(host) + ': ' + this.getLabel(item)
       	 	});
		}
	}, true));

    return nodes;
}

// 遍历AciTree，以选中、取消所有符合id的checkbox
// checked=true选中  checked=false取消
function CheckAciTreeBox(treeid, id, checked) {
	if(checked == undefined){
		checked = true;
	}
	var api = $('#'+treeid).aciTree('api');
	var children=api.children(null, true, true);
	var ids='';
	api.checkboxes(children).each(api.proxy(function(element) {
		var item = $(element);

		if(id == this.getId(item))
		{
			checked ? this.check(item) : this.uncheck(item);
		}

	}, true));
}

// 遍历AciTree，以选中、取消所有checkbox
// cancel=true取消  cancel=false选中
function UnCheckAllAciTreeBox(treeid, cancel) {
    if(cancel == undefined){
		cancel = true;
	}
	var api = $('#'+treeid).aciTree('api');
	var children=api.children(null, true, true);
	api.checkboxes(children).each(api.proxy(function(element) {
		var item = $(element);
        (cancel) ? this.uncheck(item) : this.check(item);

	}, true));
}

function UnCheckAllAciTreeRadios(treeid,exceptid)
{
	var api = $('#'+treeid).aciTree('api');

	var children=api.children(null, true, true);
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);

		if(exceptid != this.getId(item))
		{
			this.uncheck(item);
		}

	}, true));
}

function SetAciTreeValueRadio(treeid,id,key,value)
{
	var api = $('#'+treeid).aciTree('api');
	var parentid='none';
	var children=api.children(null, true, true);
	var ids='';
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);
		if(id == this.getId(item))
		{
			this.itemData(item)[key]=value;
			return false;
		}

	}, true));

}

function GetAciTreeValueRadio(treeid,id,key)
{
	var api = $('#'+treeid).aciTree('api');
	var parentid='none';
	var children=api.children(null, true, true);
	var value='';
	api.radios(children).each(api.proxy(function(element) {
		var item = $(element);
		if(id == this.getId(item))
		{
			value=this.itemData(item)[key];
			return false;
		}

	}, true));

	return value;

}

// 还原流程, 用于智能匹配驱动界面
// 遍历treeId下的box, 将满足条件的box, 不勾选
function uncheck_box_of_drivers_tree($treeid) {
	var api = $treeid.aciTree('api');
	var children=api.children(null, true, true);
	api.checkboxes(children).each(api.proxy(function(element) {
		var item = $(element);
        if(api.itemData(item).is_in_black){
        	this.uncheck(item);
		}
	}, true));
}

function getaciTreeSelected(treeid){
	var api = $('#'+treeid).aciTree('api');

	var childs=api.children(null, true, true);
	var id='';
	var label='';
	$.each(childs, function (index, item) {
        var node = $(item);
		if (api.isSelected(node))
		{
			id = String(api.getId(node));
			label = api.getLabel(node);
			return false;
		}
    });

	return {'id':id,'label':label};

}